"""
Aggressive Multi-Connection Download Engine v2
───────────────────────────────────────────────
• Worker-pool modeli: 2 MB'lık mikro-chunk'lar, boşalan worker yeni iş alır
• Persistent state: .state.json ile uygulama/bilgisayar kapansa bile devam
• EMA hız yumuşatma: 5 saniyelik pencere ile stabil hız gösterimi
• Kısmi chunk resume: yarım kalan chunk kaldığı byte'tan devam eder
"""

import asyncio
import json
import os
import re
import shutil
import time
from urllib.parse import urlparse, unquote

import aiohttp
import aiofiles

# ───────────────────────── Sabitler ─────────────────────────
MAX_RETRIES = 8
CHUNK_READ_SIZE = 131072          # 128 KB ağ okuma buffer
MERGE_BUFFER_SIZE = 16 * 1024 * 1024  # 8 MB birleştirme buffer
MICRO_CHUNK_SIZE = 4 * 1024 * 1024   # 2 MB mikro-chunk boyutu
CONNECT_TIMEOUT = 30
READ_TIMEOUT = 120
EMA_ALPHA = 0.3                   # hız yumuşatma katsayısı (0-1, küçük = daha düz)
STATE_SAVE_INTERVAL = 2.0         # saniye — state dosyası yazma aralığı


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
        self._pause_event = asyncio.Event()
        self._pause_event.set()

        # Callback'ler — GUI tarafından atanır
        self.on_progress = None       # (downloaded_total, total_size, speed_bps)
        self.on_chunk_update = None   # (chunk_idx, status_str, downloaded, size)
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
        self._state_dirty = False

    # ─────────────────── Dosya Bilgisi ───────────────────
    async def fetch_file_info(self, url: str) -> dict:
        timeout = aiohttp.ClientTimeout(total=CONNECT_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.head(url, allow_redirects=True) as resp:
                resp.raise_for_status()
                headers = resp.headers
                content_length = int(headers.get("Content-Length", 0))
                accept_ranges = headers.get("Accept-Ranges", "none").lower()
                supports_range = accept_ranges == "bytes" and content_length > 0
                filename = self._extract_filename(headers, url)
                return {
                    "size": content_length,
                    "supports_range": supports_range,
                    "filename": filename,
                    "url": str(resp.url),
                }

    @staticmethod
    def _extract_filename(headers, url: str) -> str:
        cd = headers.get("Content-Disposition", "")
        if cd:
            match = re.search(
                r'filename\*?=["\']?(?:UTF-8\'\')?([^"\';\r\n]+)',
                cd, re.IGNORECASE
            )
            if match:
                return unquote(match.group(1).strip())
        path = urlparse(url).path
        name = os.path.basename(path)
        return unquote(name) if name else "download"

    # ──────────────── Mikro-Chunk Hesaplama ────────────────
    @staticmethod
    def calculate_micro_chunks(total_size: int) -> list[tuple[int, int]]:
        """Dosyayı 2 MB'lık mikro-chunk'lara böler."""
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
    ) -> bool:
        part_path = os.path.join(temp_dir, f"{chunk.idx:06d}.part")

        for attempt in range(1, MAX_RETRIES + 1):
            if self.is_cancelled:
                return False

            chunk.attempts = attempt

            # Kısmi resume: daha önce yazılmış byte varsa
            existing_bytes = 0
            if os.path.exists(part_path):
                existing_bytes = os.path.getsize(part_path)
                if existing_bytes >= chunk.size:
                    # Zaten tamamlanmış
                    chunk.downloaded = chunk.size
                    chunk.status = ChunkInfo.COMPLETED
                    self._notify_chunk(chunk)
                    return True

            actual_start = chunk.start + existing_bytes
            chunk.downloaded = existing_bytes
            chunk.status = ChunkInfo.DOWNLOADING if attempt == 1 else ChunkInfo.RETRYING
            self._notify_chunk(chunk)

            try:
                headers = {"Range": f"bytes={actual_start}-{chunk.end}"}
                async with session.get(url, headers=headers) as resp:
                    if resp.status not in (200, 206):
                        raise aiohttp.ClientResponseError(
                            resp.request_info, resp.history,
                            status=resp.status, message=f"HTTP {resp.status}"
                        )

                    # "ab" modu: var olan verinin sonuna ekle
                    mode = "ab" if existing_bytes > 0 else "wb"
                    async with aiofiles.open(part_path, mode) as f:
                        chunk_start = time.time()
                        async for data in resp.content.iter_chunked(CHUNK_READ_SIZE):
                            await self._pause_event.wait()
                            if self.is_cancelled:
                                return False

                            await f.write(data)
                            chunk.downloaded += len(data)
                            self._downloaded_total += len(data)
                            self._notify_chunk(chunk)

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
                backoff = min(2 ** attempt, 30)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(backoff)

        chunk.status = ChunkInfo.FAILED
        self._notify_chunk(chunk)
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
                break

            ok = await self._download_chunk(session, url, chunk, temp_dir)
            results[chunk.idx] = ok
            queue.task_done()

    # ──────────────── Birleştirme ────────────────
    async def merge_chunks(self, temp_dir: str, output_path: str, chunks: list[ChunkInfo]):
        if self.on_status:
            self.on_status("Parçalar birleştiriliyor...")

        async with aiofiles.open(output_path, "wb") as out_f:
            for chunk in sorted(chunks, key=lambda c: c.idx):
                part_path = os.path.join(temp_dir, f"{chunk.idx:06d}.part")
                if not os.path.exists(part_path):
                    continue
                async with aiofiles.open(part_path, "rb") as in_f:
                    while True:
                        buf = await in_f.read(MERGE_BUFFER_SIZE)
                        if not buf:
                            break
                        await out_f.write(buf)

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
        if self._state and hasattr(self, "_state_meta"):
            m = self._state_meta
            self._state.save(
                m["url"], m["filename"], m["total_size"],
                m["connections"], m["temp_dir"], m["output_path"],
                self._chunks,
            )

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
        self._pause_event.set()
        self._downloaded_total = 0
        self._ema_speed = 0.0
        self._last_bytes = 0
        self._last_time = time.time()
        self._start_time = time.time()

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

                if not info["supports_range"] or info["size"] == 0:
                    connections = 1

                if self.on_status:
                    self.on_status(
                        f"Dosya: {filename} — {self._format_size(info['size'])}"
                    )

                output_path = os.path.join(save_dir, filename)
                temp_dir = os.path.join(save_dir, f".{filename}.temp")
                os.makedirs(temp_dir, exist_ok=True)

                # State dizini
                state_dir = os.path.join(save_dir, ".download_states")
                os.makedirs(state_dir, exist_ok=True)
                safe_name = re.sub(r'[^\w\-.]', '_', filename)
                state_path = os.path.join(state_dir, f"{safe_name}.state.json")
                self._state = DownloadState(state_path)

                if connections == 1 or not info["supports_range"]:
                    ranges = [(0, info["size"] - 1)]
                else:
                    ranges = self.calculate_micro_chunks(info["size"])

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
                )

                self._num_workers = min(connections, len(pending_chunks))

                queue = asyncio.Queue()
                for c in pending_chunks:
                    queue.put_nowait(c)

                results: dict[int, bool] = {}
                progress_task = asyncio.create_task(self._progress_loop())

                async with aiohttp.ClientSession(
                    timeout=timeout, connector=connector
                ) as session:
                    workers = [
                        asyncio.create_task(
                            self._worker(i, queue, session, real_url, temp_dir, results)
                        )
                        for i in range(min(connections, len(pending_chunks)))
                    ]
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

            elapsed = time.time() - self._start_time
            avg_speed = self._total_size / elapsed if elapsed > 0 else 0

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
            self._save_state()
            if self.on_status:
                self.on_status("İndirme duraklatıldı.")
        except Exception as e:
            self._save_state()
            if self.on_error:
                self.on_error(str(e))

    # ──────────────── Kontrol Metodları ────────────────
    def cancel(self):
        """İndirmeyi durdurur ve durumu kaydeder — devam edilebilir."""
        self.is_cancelled = True
        self._pause_event.set()
        self._save_state()

    def pause(self):
        self.is_paused = True
        self._pause_event.clear()

    def resume(self):
        self.is_paused = False
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
