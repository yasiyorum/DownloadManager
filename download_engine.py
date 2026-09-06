"""
Aggressive Multi-Connection Download Engine v3
───────────────────────────────────────────────
• Worker-pool modeli: mikro-chunk'lar, boşalan worker yeni iş alır
• Persistent state: .state.json ile uygulama/bilgisayar kapansa bile devam
• EMA hız yumuşatma: 5 saniyelik pencere ile stabil hız gösterimi
• Kısmi chunk resume: yarım kalan chunk kaldığı byte'tan devam eder
• Akıllı Fallback: HEAD reddedilirse GET (Range: 0-1) ile boyut/range tespiti
• Güvenli Dosya Adı: Windows rezerve ve geçersiz karakterleri temizleme
• Hızlı Birleştirme: Tek parçalı indirmelerde anında taşıma
"""

import asyncio
import json
import os
import re
import shutil
import time
from urllib.parse import urlparse, unquote, parse_qs

import aiohttp
import aiofiles

# ───────────────────────── Sabitler ─────────────────────────
MAX_RETRIES = 8
CHUNK_READ_SIZE = 131072               # 128 KB ağ okuma buffer
MERGE_BUFFER_SIZE = 16 * 1024 * 1024   # 16 MB birleştirme buffer
MICRO_CHUNK_SIZE = 4 * 1024 * 1024     # 4 MB mikro-chunk boyutu
CONNECT_TIMEOUT = 30
READ_TIMEOUT = 120
EMA_ALPHA = 0.3                        # Hız yumuşatma katsayısı
STATE_SAVE_INTERVAL = 2.0              # Saniye — state dosyası yazma aralığı

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/134.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
}


def sanitize_filename(name: str) -> str:
    """Windows ve Linux için dosya adını güvenli hale getirir."""
    if not name:
        return f"download_{int(time.time())}"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = name.strip(". ")
    reserved = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4",
                "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2",
                "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}
    base, ext = os.path.splitext(name)
    if base.upper() in reserved:
        name = f"_{name}"
    if not name or name == "_":
        name = f"download_{int(time.time())}"
    return name



class ChunkInfo:
    """Tek bir chunk'ın bilgisini tutar (serializable)."""
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"

    def __init__(self, idx: int, start: int, end: int,
                 downloaded: int = 0, status: str = "pending"):
        self.idx = idx
        self.start = start
        self.end = end
        self.size = end - start + 1
        self.downloaded = downloaded
        self.status = status
        self.attempts = 0

    def to_dict(self) -> dict:
        return {
            "idx": self.idx, "start": self.start, "end": self.end,
            "downloaded": self.downloaded, "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChunkInfo":
        return cls(d["idx"], d["start"], d["end"], d["downloaded"], d["status"])


class DownloadState:
    """İndirme durumunu diske kalıcı olarak kaydeder/yükler."""

    def __init__(self, state_path: str):
        self.path = state_path

    def save(self, url: str, filename: str, total_size: int,
             connections: int, temp_dir: str, output_path: str,
             chunks: list[ChunkInfo]):
        data = {
            "url": url,
            "filename": filename,
            "total_size": total_size,
            "connections": connections,
            "temp_dir": temp_dir,
            "output_path": output_path,
            "chunks": [c.to_dict() for c in chunks],
            "timestamp": time.time(),
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # Atomik değiştirme
        if os.path.exists(self.path):
            os.remove(self.path)
        os.rename(tmp, self.path)

    def load(self) -> dict | None:
        if not os.path.exists(self.path):
            return None
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["chunks"] = [ChunkInfo.from_dict(c) for c in data["chunks"]]
            return data
        except Exception:
            return None

    def delete(self):
        for p in (self.path, self.path + ".tmp"):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass


class DownloadEngine:
    """
    Asenkron çoklu bağlantılı indirme motoru v2.
    Worker-pool modeli ile stabil bant genişliği kullanımı.
    Persistent state ile kalıcı duraklatma/devam desteği.
    """

    def __init__(self):
        self.is_cancelled = False
        self.is_paused = False
        self._pause_event = None  # Lazy init in start()
        self._completed = False   # Reliable completion flag

        # Callback'ler — GUI tarafından atanır
        self.on_progress = None       # (downloaded_total, total_size, speed_bps)
        self.on_chunk_update = None   # (chunk_idx, status_str, downloaded, size)
        self.on_worker_update = None  # (worker_id, chunk_idx, downloaded, size, status, speed)
        self.on_status = None         # (status_text)
        self.on_complete = None       # (output_path)
        self.on_error = None          # (error_text)

        # Hız sınırlama
        self.speed_limit = 0          # bytes/s, 0 = sınırsız
        self._num_workers = 1

        # İç durum
        self._chunks: list[ChunkInfo] = []
        self._downloaded_total = 0
        self._total_size = 0
        self._start_time = 0.0
        self._ema_speed = 0.0
        self._last_bytes = 0
        self._last_time = 0.0
        self._state: DownloadState | None = None
        self._state_meta: dict | None = None
        self._state_dirty = False
        self._active_tasks: list[asyncio.Task] = []
        self._session: aiohttp.ClientSession | None = None

    # ─────────────────── Dosya Bilgisi ───────────────────
    async def fetch_file_info(self, url: str) -> dict:
        """
        Dosya boyutunu, range desteğini ve adını tespit eder.
        HEAD reddedilirse GET (Range: 0-1) fallback uygular.
        """
        timeout = aiohttp.ClientTimeout(total=CONNECT_TIMEOUT, connect=15)
        connector = aiohttp.TCPConnector(ssl=False)

        # 1. HEAD isteği dene
        try:
            async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=DEFAULT_HEADERS) as session:
                async with session.head(url, allow_redirects=True) as resp:
                    headers = resp.headers
                    final_url = str(resp.url)
                    content_length = int(headers.get("Content-Length", 0))
                    accept_ranges = headers.get("Accept-Ranges", "none").lower()
                    supports_range = (accept_ranges == "bytes" or "bytes" in accept_ranges) and content_length > 0
                    filename = self._extract_filename(headers, final_url)

                    if resp.status in (200, 206) and content_length > 0:
                        return {
                            "size": content_length,
                            "supports_range": supports_range,
                            "filename": filename,
                            "url": final_url,
                        }
        except Exception:
            pass

        # 2. GET (Range: 0-1) fallback (Cloudflare/Google Drive gibi HEAD engelleyenler için)
        try:
            range_headers = {**DEFAULT_HEADERS, "Range": "bytes=0-1"}
            connector2 = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(timeout=timeout, connector=connector2, headers=range_headers) as session:
                async with session.get(url, allow_redirects=True) as resp:
                    headers = resp.headers
                    final_url = str(resp.url)
                    filename = self._extract_filename(headers, final_url)

                    if resp.status == 206:
                        cr = headers.get("Content-Range", "")
                        match = re.search(r"/(\d+)$", cr)
                        total = int(match.group(1)) if match else int(headers.get("Content-Length", 0))
                        return {
                            "size": total,
                            "supports_range": True,
                            "filename": filename,
                            "url": final_url,
                        }
                    elif resp.status == 200:
                        total = int(headers.get("Content-Length", 0))
                        return {
                            "size": total,
                            "supports_range": False,
                            "filename": filename,
                            "url": final_url,
                        }
        except Exception:
            pass

        return {
            "size": 0,
            "supports_range": False,
            "filename": self._extract_filename({}, url),
            "url": url,
        }

    @staticmethod
    def _extract_filename(headers, url: str) -> str:
        """RFC uyumlu dosya adı çıkarma ve URL parametre analizi."""
        # 1. Content-Disposition
        cd = headers.get("Content-Disposition", "") if headers else ""
        if cd:
            m_utf8 = re.search(r"filename\*\s*=\s*(?:UTF-8''|utf-8'')([^;\r\n]+)", cd, re.IGNORECASE)
            if m_utf8:
                return sanitize_filename(unquote(m_utf8.group(1).strip().strip('"\'')))
            m = re.search(r'filename\s*=\s*"?([^";\r\n]+)"?', cd, re.IGNORECASE)
            if m:
                return sanitize_filename(unquote(m.group(1).strip().strip('"\'')))

        # 2. URL Query Parametreleri
        parsed = urlparse(url)
        if parsed.query:
            qs = parse_qs(parsed.query)
            for k in ("file", "filename", "name", "dl_file", "response-content-disposition"):
                if k in qs and qs[k]:
                    val = qs[k][0]
                    if "." in val:
                        return sanitize_filename(unquote(val))

        # 3. URL Yolu
        path = unquote(parsed.path)
        name = os.path.basename(path)
        if name:
            return sanitize_filename(name)

        return f"download_{int(time.time())}"

    # ──────────────── Mikro-Chunk Hesaplama ────────────────
    @staticmethod
    def calculate_micro_chunks(total_size: int) -> list[tuple[int, int]]:
        """Dosyayı mikro-chunk'lara böler."""
        if total_size <= 0:
            return []
        chunks = []
        offset = 0
        while offset < total_size:
            end = min(offset + MICRO_CHUNK_SIZE - 1, total_size - 1)
            chunks.append((offset, end))
            offset = end + 1
        return chunks

    # ──────────────── Tekil Chunk İndirme ────────────────
    async def _download_chunk(
        self,
        session: aiohttp.ClientSession,
        url: str,
        chunk: ChunkInfo,
        temp_dir: str,
        worker_id: int = 0,
    ) -> bool:
        part_path = os.path.join(temp_dir, f"{chunk.idx:06d}.part")

        for attempt in range(1, MAX_RETRIES + 1):
            if self.is_cancelled:
                return False

            if self._pause_event:
                await self._pause_event.wait()
            if self.is_cancelled:
                return False

            chunk.attempts = attempt

            # Kısmi resume: daha önce yazılmış byte varsa
            existing_bytes = 0
            if os.path.exists(part_path):
                existing_bytes = os.path.getsize(part_path)
                if existing_bytes >= chunk.size and chunk.size > 0:
                    chunk.downloaded = chunk.size
                    chunk.status = ChunkInfo.COMPLETED
                    self._notify_chunk(chunk)
                    self._notify_worker(worker_id, chunk.idx, chunk.size, chunk.size, "completed")
                    return True

            actual_start = chunk.start + existing_bytes
            chunk.downloaded = existing_bytes
            chunk.status = ChunkInfo.DOWNLOADING if attempt == 1 else ChunkInfo.RETRYING
            self._notify_chunk(chunk)
            self._notify_worker(worker_id, chunk.idx, existing_bytes, chunk.size, chunk.status)

            try:
                headers = {**DEFAULT_HEADERS, "Range": f"bytes={actual_start}-{chunk.end}"}
                async with session.get(url, headers=headers) as resp:
                    if resp.status not in (200, 206):
                        raise aiohttp.ClientResponseError(
                            resp.request_info, resp.history,
                            status=resp.status, message=f"HTTP {resp.status}"
                        )

                    mode = "ab" if (existing_bytes > 0 and resp.status == 206) else "wb"
                    if resp.status == 200 and actual_start > 0:
                        chunk.downloaded = 0
                        mode = "wb"

                    async with aiofiles.open(part_path, mode) as f:
                        chunk_start = time.time()
                        last_notify = time.time()

                        async for data in resp.content.iter_chunked(CHUNK_READ_SIZE):
                            if self._pause_event:
                                await self._pause_event.wait()
                            if self.is_cancelled:
                                return False

                            await f.write(data)
                            d_len = len(data)
                            chunk.downloaded += d_len
                            self._downloaded_total += d_len

                            now = time.time()
                            if now - last_notify > 0.15:
                                self._notify_chunk(chunk)
                                self._notify_worker(worker_id, chunk.idx, chunk.downloaded, chunk.size, "downloading")
                                last_notify = now

                            # Hız sınırlama
                            if self.speed_limit > 0:
                                per_worker = self.speed_limit / max(self._num_workers, 1)
                                elapsed = time.time() - chunk_start
                                expected = chunk.downloaded / per_worker
                                if expected > elapsed:
                                    await asyncio.sleep(expected - elapsed)

                if chunk.downloaded >= chunk.size * 0.99:
                    chunk.status = ChunkInfo.COMPLETED
                    chunk.downloaded = chunk.size
                    self._notify_chunk(chunk)
                    self._notify_worker(worker_id, chunk.idx, chunk.size, chunk.size, "completed")
                    self._state_dirty = True
                    return True
                else:
                    raise IOError(
                        f"Chunk {chunk.idx}: eksik veri "
                        f"({chunk.downloaded}/{chunk.size})"
                    )

            except asyncio.CancelledError:
                return False
            except Exception:
                if self.is_cancelled:
                    return False
                backoff = min(2 ** attempt, 15)
                self._notify_worker(worker_id, chunk.idx, chunk.downloaded, chunk.size, "retrying")
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(backoff)

        chunk.status = ChunkInfo.FAILED
        self._notify_chunk(chunk)
        self._notify_worker(worker_id, chunk.idx, chunk.downloaded, chunk.size, "failed")
        return False

    # ──────────────── Worker Pool ────────────────
    async def _worker(self, worker_id: int, queue: asyncio.Queue,
                      session: aiohttp.ClientSession, url: str,
                      temp_dir: str, results: dict):
        """Queue'dan chunk alıp indirir, boşalınca yeni iş alır."""
        while not self.is_cancelled:
            try:
                chunk: ChunkInfo = queue.get_nowait()
            except asyncio.QueueEmpty:
                self._notify_worker(worker_id, None, 0, 0, "idle")
                break

            ok = await self._download_chunk(session, url, chunk, temp_dir, worker_id=worker_id)
            results[chunk.idx] = ok
            queue.task_done()

    # ──────────────── Tek Akış İndirme (Range Desteksiz / Bilinmeyen Boyut) ────────────────
    async def _download_single_stream(self, url: str, output_path: str) -> bool:
        """Range desteklemeyen veya boyutu belirsiz dosyalar için kesintisiz akış."""
        timeout = aiohttp.ClientTimeout(connect=CONNECT_TIMEOUT, sock_read=READ_TIMEOUT, total=None)
        connector = aiohttp.TCPConnector(ssl=False)
        self._num_workers = 1
        self._notify_worker(0, 0, 0, self._total_size, "downloading")

        async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=DEFAULT_HEADERS) as session:
            self._session = session
            progress_task = asyncio.create_task(self._progress_loop())
            self._active_tasks.append(progress_task)

            try:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    async with aiofiles.open(output_path, "wb") as f:
                        last_notify = time.time()
                        chunk_start = time.time()

                        async for data in resp.content.iter_chunked(CHUNK_READ_SIZE):
                            if self._pause_event:
                                await self._pause_event.wait()
                            if self.is_cancelled:
                                return False

                            await f.write(data)
                            d_len = len(data)
                            self._downloaded_total += d_len

                            now = time.time()
                            if now - last_notify > 0.2:
                                self._notify_worker(0, 0, self._downloaded_total, self._total_size, "downloading")
                                last_notify = now

                            if self.speed_limit > 0:
                                elapsed = time.time() - chunk_start
                                expected = self._downloaded_total / self.speed_limit
                                if expected > elapsed:
                                    await asyncio.sleep(expected - elapsed)

                self._completed = True
                self._notify_worker(0, 0, self._downloaded_total, self._total_size or self._downloaded_total, "completed")
                return True
            except asyncio.CancelledError:
                return False
            finally:
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass

    # ──────────────── Birleştirme ────────────────
    async def merge_chunks(self, temp_dir: str, output_path: str, chunks: list[ChunkInfo]):
        """Tamamlanan chunk'ları birleştirir. Tek parça ise anında taşır."""
        valid_chunks = [c for c in chunks if c.status == ChunkInfo.COMPLETED]
        if not valid_chunks:
            return

        # 1. Tek parça ise anında taşı (Sıfır ek disk okuma/yazma)
        if len(valid_chunks) == 1:
            part_path = os.path.join(temp_dir, f"{valid_chunks[0].idx:06d}.part")
            if os.path.exists(part_path):
                if os.path.exists(output_path):
                    try:
                        os.remove(output_path)
                    except OSError:
                        pass
                shutil.move(part_path, output_path)
                return

        # 2. Çoklu parçaları birleştir
        if self.on_status:
            self.on_status("Parçalar birleştiriliyor...")

        total_bytes = sum(c.size for c in valid_chunks)
        merged_bytes = 0

        async with aiofiles.open(output_path, "wb") as out_f:
            for chunk in sorted(valid_chunks, key=lambda c: c.idx):
                part_path = os.path.join(temp_dir, f"{chunk.idx:06d}.part")
                if not os.path.exists(part_path):
                    continue
                async with aiofiles.open(part_path, "rb") as in_f:
                    while True:
                        buf = await in_f.read(MERGE_BUFFER_SIZE)
                        if not buf:
                            break
                        await out_f.write(buf)
                        merged_bytes += len(buf)
                        if total_bytes > 0 and self.on_status:
                            pct = (merged_bytes / total_bytes) * 100
                            self.on_status(f"Parçalar birleştiriliyor: %{pct:.0f}...")

    # ──────────────── Hız Hesaplama (EMA) ────────────────
    def _calc_speed(self) -> float:
        """EMA ile yumuşatılmış hız hesaplar — dalgalanma olmaz."""
        now = time.time()
        dt = now - self._last_time
        if dt < 0.2:
            return self._ema_speed

        bytes_delta = self._downloaded_total - self._last_bytes
        instant_speed = bytes_delta / dt if dt > 0 else 0

        if self._ema_speed == 0:
            self._ema_speed = instant_speed
        else:
            self._ema_speed = EMA_ALPHA * instant_speed + (1 - EMA_ALPHA) * self._ema_speed

        self._last_bytes = self._downloaded_total
        self._last_time = now
        return self._ema_speed

    # ──────────────── Bildiriciler ────────────────
    def _notify_chunk(self, chunk: ChunkInfo):
        if self.on_chunk_update:
            self.on_chunk_update(chunk.idx, chunk.status, chunk.downloaded, chunk.size)

    def _notify_worker(self, worker_id: int, chunk_idx: int | None, downloaded: int, size: int, status: str):
        if self.on_worker_update:
            self.on_worker_update(worker_id, chunk_idx, downloaded, size, status)

    async def _progress_loop(self):
        """Periyodik ilerleme + state kaydetme."""
        last_state_save = 0.0
        while not self.is_cancelled:
            speed = self._calc_speed()
            if self.on_progress:
                self.on_progress(self._downloaded_total, self._total_size, speed)

            # Periyodik state kaydetme
            now = time.time()
            if self._state and (self._state_dirty or now - last_state_save > STATE_SAVE_INTERVAL):
                self._save_state()
                self._state_dirty = False
                last_state_save = now

            await asyncio.sleep(0.3)

    def _save_state(self):
        """Mevcut durumu diske yazar."""
        if self._completed or not self._state or not hasattr(self, "_state_meta") or not self._state_meta:
            return
        try:
            m = self._state_meta
            self._state.save(
                m["url"], m["filename"], m["total_size"],
                m["connections"], m["temp_dir"], m["output_path"],
                self._chunks,
            )
        except Exception:
            pass

    # ──────────────── Yarıda Kalan İndirmeleri Bul ────────────────
    @staticmethod
    def find_pending_downloads(save_dir: str) -> list[dict]:
        """Verilen dizinde yarım kalmış indirmeleri bulur."""
        pending = []
        state_dir = os.path.join(save_dir, ".download_states")
        if not os.path.isdir(state_dir):
            return pending

        for fname in os.listdir(state_dir):
            if fname.endswith(".state.json"):
                state = DownloadState(os.path.join(state_dir, fname))
                data = state.load()
                if data:
                    completed = sum(1 for c in data["chunks"]
                                    if c.status == ChunkInfo.COMPLETED)
                    total_chunks = len(data["chunks"])
                    downloaded_bytes = sum(c.downloaded for c in data["chunks"])
                    if completed < total_chunks:
                        pending.append({
                            "state": state,
                            "data": data,
                            "completed_chunks": completed,
                            "total_chunks": total_chunks,
                            "downloaded_bytes": downloaded_bytes,
                        })
        return pending

    # ──────────────── Ana Orkestrasyon ────────────────
    async def start(self, url: str, connections: int, save_dir: str,
                    resume_state: dict | None = None):
        """
        İndirme sürecini başlatır veya yarıda kalandan devam eder.
        resume_state verilmişse kaydedilmiş durumdan devam eder.
        """
        self.is_cancelled = False
        self.is_paused = False
        self._completed = False
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._downloaded_total = 0
        self._ema_speed = 0.0
        self._last_bytes = 0
        self._last_time = time.time()
        self._start_time = time.time()
        self._active_tasks.clear()

        try:
            if resume_state:
                # ── DEVAM MODU ──
                data = resume_state["data"]
                real_url = data["url"]
                self._total_size = data["total_size"]
                filename = data["filename"]
                temp_dir = data["temp_dir"]
                output_path = data["output_path"]
                connections = data["connections"]
                self._chunks = data["chunks"]
                self._state = resume_state["state"]

                # Tamamlanmış chunk'ların byte'larını say
                already_done = sum(
                    c.size for c in self._chunks if c.status == ChunkInfo.COMPLETED
                )
                # Yarım kalan chunk'ların kısmi byte'ları
                for c in self._chunks:
                    if c.status != ChunkInfo.COMPLETED:
                        part_path = os.path.join(temp_dir, f"{c.idx:06d}.part")
                        if os.path.exists(part_path):
                            c.downloaded = os.path.getsize(part_path)
                        else:
                            c.downloaded = 0
                        c.status = ChunkInfo.PENDING
                        already_done += c.downloaded

                self._downloaded_total = already_done

                if self.on_status:
                    self.on_status(
                        f"Devam ediliyor: {filename} — "
                        f"{self._format_size(already_done)} / "
                        f"{self._format_size(self._total_size)}"
                    )

                # Tamamlanmamış chunk'ları queue'ya ekle
                pending_chunks = [
                    c for c in self._chunks if c.status != ChunkInfo.COMPLETED
                ]

            else:
                # ── YENİ İNDİRME ──
                if self.on_status:
                    self.on_status("Dosya bilgisi alınıyor...")
                info = await self.fetch_file_info(url)
                real_url = info["url"]
                self._total_size = info["size"]
                filename = info["filename"]
                supports_range = info["supports_range"]

                # Dosya adı çakışmasını önle
                output_path = os.path.join(save_dir, filename)
                if os.path.exists(output_path):
                    base_n, ext_n = os.path.splitext(filename)
                    counter = 1
                    while os.path.exists(os.path.join(save_dir, f"{base_n} ({counter}){ext_n}")):
                        counter += 1
                    filename = f"{base_n} ({counter}){ext_n}"
                    output_path = os.path.join(save_dir, filename)

                if self.on_status:
                    size_str = self._format_size(self._total_size) if self._total_size > 0 else "Bilinmiyor"
                    self.on_status(f"Dosya: {filename} — {size_str}")

                # Range desteklenmiyorsa veya boyut 0 ise: Tek Akış İndirme
                if not supports_range or self._total_size <= 0:
                    ok = await self._download_single_stream(real_url, output_path)
                    if ok:
                        elapsed = time.time() - self._start_time
                        avg_spd = self._downloaded_total / elapsed if elapsed > 0 else 0
                        if self.on_complete:
                            self.on_complete(output_path)
                        if self.on_status:
                            self.on_status(f"✅ Tamamlandı — {self._format_size(self._downloaded_total)} ({self._format_time(elapsed)})")
                    return

                temp_dir = os.path.join(save_dir, f".{filename}.temp")
                os.makedirs(temp_dir, exist_ok=True)

                # State dizini
                state_dir = os.path.join(save_dir, ".download_states")
                os.makedirs(state_dir, exist_ok=True)
                safe_name = re.sub(r'[^\w\-.]', '_', filename)
                state_path = os.path.join(state_dir, f"{safe_name}.state.json")
                self._state = DownloadState(state_path)

                ranges = self.calculate_micro_chunks(self._total_size)
                self._chunks = [ChunkInfo(i, s, e) for i, (s, e) in enumerate(ranges)]
                pending_chunks = list(self._chunks)

            # State meta bilgisi
            self._state_meta = {
                "url": real_url, "filename": filename,
                "total_size": self._total_size, "connections": connections,
                "temp_dir": temp_dir, "output_path": output_path,
            }

            # İlk state kaydı
            self._save_state()

            # Tüm chunk'ları GUI'ye bildir
            for c in self._chunks:
                self._notify_chunk(c)

            if not pending_chunks:
                # Tüm chunk'lar zaten tamamlanmış — doğrudan merge
                if self.on_status:
                    self.on_status("Tüm parçalar mevcut, birleştiriliyor...")
            else:
                if self.on_status:
                    total_c = len(self._chunks)
                    done_c = total_c - len(pending_chunks)
                    self.on_status(
                        f"{total_c} parça, {connections} bağlantı "
                        f"({done_c} tamamlanmış, {len(pending_chunks)} kalan)"
                    )

                # Worker pool
                timeout = aiohttp.ClientTimeout(
                    connect=CONNECT_TIMEOUT,
                    sock_read=READ_TIMEOUT,
                    total=None,
                )
                connector = aiohttp.TCPConnector(
                    limit=connections + 5,
                    limit_per_host=connections + 5,
                    enable_cleanup_closed=True,
                    force_close=False,
                    ttl_dns_cache=300,
                    ssl=False,
                )

                self._num_workers = min(connections, len(pending_chunks))

                queue = asyncio.Queue()
                for c in pending_chunks:
                    queue.put_nowait(c)

                results: dict[int, bool] = {}
                progress_task = asyncio.create_task(self._progress_loop())
                self._active_tasks.append(progress_task)

                async with aiohttp.ClientSession(
                    timeout=timeout, connector=connector, headers=DEFAULT_HEADERS
                ) as session:
                    self._session = session
                    workers = [
                        asyncio.create_task(
                            self._worker(i, queue, session, real_url, temp_dir, results)
                        )
                        for i in range(self._num_workers)
                    ]
                    self._active_tasks.extend(workers)
                    await asyncio.gather(*workers, return_exceptions=True)

                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass

                # Son state kaydı
                self._save_state()

                if self.is_cancelled:
                    if self.on_status:
                        self.on_status("İndirme duraklatıldı — kaldığı yerden devam edilebilir.")
                    return

                # Hata kontrolü
                failed_chunks = [
                    c for c in self._chunks
                    if c.status != ChunkInfo.COMPLETED
                ]
                if failed_chunks:
                    err = f"{len(failed_chunks)} parça indirilemedi"
                    if self.on_error:
                        self.on_error(err)
                    return

            # 4) Birleştirme
            await self.merge_chunks(temp_dir, output_path, self._chunks)

            # Temizlik
            self._cleanup(temp_dir)
            if self._state:
                self._state.delete()
                # Eğer .download_states boşsa klasörü de sil
                state_dir = os.path.dirname(self._state.path)
                self._cleanup_empty_dir(state_dir)
                self._state = None

            elapsed = time.time() - self._start_time
            avg_speed = self._total_size / elapsed if elapsed > 0 else 0

            self._completed = True
            if self.on_progress:
                self.on_progress(self._total_size, self._total_size, avg_speed)
            if self.on_status:
                self.on_status(
                    f"✅ Tamamlandı — {self._format_size(self._total_size)} "
                    f"({self._format_time(elapsed)}, ort. {self._format_speed(avg_speed)})"
                )
            if self.on_complete:
                self.on_complete(output_path)

        except asyncio.CancelledError:
            if not self._completed:
                self._save_state()
            if self.on_status:
                self.on_status("İndirme duraklatıldı.")
        except Exception as e:
            if not self._completed:
                self._save_state()
            if self.on_error:
                self.on_error(str(e))

    # ──────────────── Kontrol Metodları ────────────────
    def cancel(self):
        """İndirmeyi durdurur ve durumu kaydeder — devam edilebilir."""
        self.is_cancelled = True
        if self._pause_event:
            self._pause_event.set()
        for t in self._active_tasks:
            if not t.done():
                t.cancel()
        self._save_state()

    def pause(self):
        self.is_paused = True
        if self._pause_event:
            self._pause_event.clear()

    def resume(self):
        self.is_paused = False
        if self._pause_event:
            self._pause_event.set()

    # ──────────────── Yardımcı Metodlar ────────────────
    @staticmethod
    def _cleanup(temp_dir: str):
        try:
            if os.path.isdir(temp_dir):
                shutil.rmtree(temp_dir)
        except OSError:
            pass

    @staticmethod
    def _cleanup_empty_dir(dir_path: str):
        """Klasör boşsa sil."""
        try:
            if os.path.isdir(dir_path) and not os.listdir(dir_path):
                os.rmdir(dir_path)
        except OSError:
            pass

    @staticmethod
    def _format_size(b: float) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if b < 1024:
                return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} PB"

    @staticmethod
    def _format_speed(bps: float) -> str:
        return DownloadEngine._format_size(bps) + "/s"

    @staticmethod
    def _format_time(seconds: float) -> str:
        if seconds < 0:
            return "--"
        if seconds < 60:
            return f"{seconds:.0f}s"
        m, s = divmod(int(seconds), 60)
        if m < 60:
            return f"{m}dk {s}s"
        h, m = divmod(m, 60)
        return f"{h}sa {m}dk {s}s"
