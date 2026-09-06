"""
features.py — Tüm yardımcı özellikler
─────────────────────────────────────
İndirme geçmişi, ayarlar, kategorilendirme, arşiv açma,
bildirimler, otomatik başlangıç, tarayıcı kurulumu,
video indirici, zamanlayıcı
"""

import json, os, re, shutil, sqlite3, subprocess, sys
import threading, time, webbrowser, zipfile
from datetime import datetime, timedelta
from pathlib import Path

try:
    import winreg
    HAS_WINREG = True
except ImportError:
    HAS_WINREG = False

APP_DIR = os.path.join(os.path.expanduser("~"), ".agresif_dm")
os.makedirs(APP_DIR, exist_ok=True)


# ═══════════════════ Ayar Yöneticisi ═══════════════════
class SettingsManager:
    DEFAULTS = {
        "speed_limit": 0,
        "auto_extract": False,
        "auto_categorize": False,
        "default_connections": 16,
        "notify_on_complete": True,
        "auto_start": False,
        "save_dir": os.path.expanduser("~/Downloads"),
        "categories": {
            "Video": [".mp4",".mkv",".avi",".mov",".wmv",".flv",".webm"],
            "Müzik": [".mp3",".flac",".wav",".aac",".ogg",".wma"],
            "Resim": [".jpg",".jpeg",".png",".gif",".bmp",".svg",".webp"],
            "Belge": [".pdf",".doc",".docx",".xls",".xlsx",".ppt",".pptx",".txt",".csv"],
            "Arşiv": [".zip",".rar",".7z",".tar",".gz",".bz2",".tgz"],
            "Program": [".exe",".msi",".dmg",".deb",".rpm",".apk"],
            "Disk İmajı": [".iso",".img"],
        },
    }

    def __init__(self):
        self.path = os.path.join(APP_DIR, "settings.json")
        self._data = dict(self.DEFAULTS)
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data.update(json.load(f))
            except Exception:
                pass

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get(self, key, default=None):
        return self._data.get(key, default if default is not None else self.DEFAULTS.get(key))

    def set(self, key, value):
        self._data[key] = value
        self.save()


# ═══════════════════ İndirme Geçmişi ═══════════════════
class DownloadHistory:
    def __init__(self):
        self.db_path = os.path.join(APP_DIR, "history.db")
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT, filename TEXT, size INTEGER,
                status TEXT, path TEXT,
                date TEXT, speed_avg REAL)""")

    def add(self, url, filename, size=0, status="completed", path="", speed=0):
        with sqlite3.connect(self.db_path) as c:
            c.execute("INSERT INTO history (url,filename,size,status,path,date,speed_avg) VALUES (?,?,?,?,?,?,?)",
                      (url, filename, size, status, path, datetime.now().strftime("%Y-%m-%d %H:%M"), speed))

    def get_all(self, limit=200):
        with sqlite3.connect(self.db_path) as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute("SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def search(self, q):
        with sqlite3.connect(self.db_path) as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute(
                "SELECT * FROM history WHERE filename LIKE ? OR url LIKE ? ORDER BY id DESC",
                (f"%{q}%", f"%{q}%")).fetchall()]

    def clear(self):
        with sqlite3.connect(self.db_path) as c:
            c.execute("DELETE FROM history")

    def delete(self, rid):
        with sqlite3.connect(self.db_path) as c:
            c.execute("DELETE FROM history WHERE id=?", (rid,))


# ═══════════════════ Dosya Kategorilendirme ═══════════════════
class FileCategorizer:
    @staticmethod
    def get_category(filename, categories):
        ext = os.path.splitext(filename)[1].lower()
        for cat, exts in categories.items():
            if ext in exts:
                return cat
        return "Diğer"

    @staticmethod
    def move_to_category(filepath, base_dir, categories):
        if not filepath or not os.path.isfile(filepath):
            return filepath
        fn = os.path.basename(filepath)
        cat = FileCategorizer.get_category(fn, categories)
        dest_dir = os.path.join(base_dir, cat)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, fn)
        if os.path.exists(dest):
            name, ext = os.path.splitext(fn)
            dest = os.path.join(dest_dir, f"{name}_{int(time.time())}{ext}")
        try:
            shutil.move(filepath, dest)
            return dest
        except Exception:
            return filepath


# ═══════════════════ Otomatik Arşiv Açma ═══════════════════
class AutoExtractor:
    SUPPORTED = {".zip", ".tar", ".gz", ".bz2", ".tgz", ".tar.gz", ".tar.bz2"}

    @staticmethod
    def can_extract(filename):
        return any(filename.lower().endswith(e) for e in AutoExtractor.SUPPORTED)

    @staticmethod
    def extract(filepath, dest=None):
        if not filepath or not os.path.isfile(filepath):
            return None
        if dest is None:
            name = os.path.splitext(os.path.basename(filepath))[0]
            if name.endswith(".tar"):
                name = name[:-4]
            dest = os.path.join(os.path.dirname(filepath), name)
        os.makedirs(dest, exist_ok=True)
        try:
            if filepath.lower().endswith(".zip"):
                with zipfile.ZipFile(filepath, "r") as zf:
                    zf.extractall(dest)
            else:
                shutil.unpack_archive(filepath, dest)
            return dest
        except Exception:
            return None


# ═══════════════════ Bildirimler ═══════════════════
class Notifications:
    @staticmethod
    def show(title, message):
        try:
            ps = (
                '[void][System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms");'
                '$n=New-Object System.Windows.Forms.NotifyIcon;'
                '$n.Icon=[System.Drawing.SystemIcons]::Information;'
                f'$n.BalloonTipTitle="{title}";'
                f'$n.BalloonTipText="{message}";'
                '$n.Visible=$True;$n.ShowBalloonTip(4000);'
                'Start-Sleep -Seconds 5;$n.Dispose()'
            )
            subprocess.Popen(["powershell", "-WindowStyle", "Hidden", "-Command", ps],
                             creationflags=0x08000000)
        except Exception:
            pass


# ═══════════════════ Otomatik Başlangıç ═══════════════════
class AutoStart:
    KEY = "AgresifIndirmeYoneticisi"

    @staticmethod
    def enable():
        if not HAS_WINREG: return False
        try:
            cmd = f'"{sys.executable}" "{os.path.abspath(os.path.join(os.path.dirname(__file__), "main.py"))}"'
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(k, AutoStart.KEY, 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(k)
            return True
        except Exception:
            return False

    @staticmethod
    def disable():
        if not HAS_WINREG: return False
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(k, AutoStart.KEY)
            winreg.CloseKey(k)
            return True
        except Exception:
            return False

    @staticmethod
    def is_enabled():
        if not HAS_WINREG: return False
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_QUERY_VALUE)
            winreg.QueryValueEx(k, AutoStart.KEY)
            winreg.CloseKey(k)
            return True
        except Exception:
            return False



# ═══════════════════ Clipboard İzleyici ═══════════════════
class ClipboardWatcher:
    """Panoyu (clipboard) izler, yeni indirilebilir URL algılar."""

    # İndirilebilir dosya uzantıları
    DOWNLOAD_EXTENSIONS = {
        '.exe', '.msi', '.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.tgz',
        '.iso', '.img', '.dmg', '.deb', '.rpm', '.apk',
        '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm',
        '.mp3', '.flac', '.wav', '.aac', '.ogg', '.wma',
        '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp',
        '.torrent',
    }

    # Video platform desenleri
    VIDEO_PATTERNS = [
        r'(?:https?://)?(?:www\.)?youtube\.com/watch',
        r'(?:https?://)?youtu\.be/',
        r'(?:https?://)?(?:www\.)?youtube\.com/shorts/',
        r'(?:https?://)?(?:www\.)?youtube\.com/playlist',
        r'(?:https?://)?(?:www\.)?vimeo\.com/',
        r'(?:https?://)?(?:www\.)?dailymotion\.com/video/',
        r'(?:https?://)?(?:www\.)?twitch\.tv/',
        r'(?:https?://)?(?:www\.)?twitter\.com/.*/status/',
        r'(?:https?://)?(?:www\.)?x\.com/.*/status/',
        r'(?:https?://)?(?:www\.)?instagram\.com/(p|reel|tv)/',
        r'(?:https?://)?(?:www\.)?tiktok\.com/',
        r'(?:https?://)?(?:www\.)?facebook\.com/.*/videos/',
        r'(?:https?://)?(?:www\.)?reddit\.com/.*',
        r'(?:https?://)?(?:www\.)?bilibili\.com/video/',
        r'(?:https?://)?(?:www\.)?soundcloud\.com/',
    ]

    def __init__(self):
        self._running = False
        self._thread = None
        self._last_url = ""
        self.on_url_detected = None  # callback(url, is_video)

    @staticmethod
    def is_download_url(text: str) -> bool:
        """Metin bir indirilebilir URL mi?"""
        text = text.strip()
        if not text.startswith(('http://', 'https://')):
            return False
        # Magnet link
        if text.startswith('magnet:?'):
            return True
        # Bilinen video platformları
        for pattern in ClipboardWatcher.VIDEO_PATTERNS:
            if re.match(pattern, text, re.IGNORECASE):
                return True
        # Dosya uzantısı kontrolü
        from urllib.parse import urlparse
        path = urlparse(text).path.lower()
        for ext in ClipboardWatcher.DOWNLOAD_EXTENSIONS:
            if path.endswith(ext):
                return True
        # Genel büyük dosya URL'leri (query string'de dosya adı olanlar) 
        if re.search(r'[?&](file|name|filename|dl)=', text, re.IGNORECASE):
            return True
        return False

    @staticmethod
    def is_video_url(text: str) -> bool:
        """URL bir video platformundan mı?"""
        text = text.strip()
        for pattern in ClipboardWatcher.VIDEO_PATTERNS:
            if re.match(pattern, text, re.IGNORECASE):
                return True
        return False

    def start(self, root):
        """Clipboard izlemeyi başlat. root = tkinter root widget."""
        self._running = True
        self._root = root
        self._poll()

    def _poll(self):
        """Her 1.5 saniyede panoyu kontrol et."""
        if not self._running:
            return
        try:
            text = self._root.clipboard_get().strip()
            if text and text != self._last_url and self.is_download_url(text):
                self._last_url = text
                is_video = self.is_video_url(text)
                if self.on_url_detected:
                    self.on_url_detected(text, is_video)
        except Exception:
            pass  # Clipboard boş veya erişilemez
        self._root.after(1500, self._poll)

    def stop(self):
        self._running = False

    def mark_url_seen(self, url: str):
        """Bir URL'yi görüldü olarak işaretle (tekrar popup açmasın)."""
        self._last_url = url


# ═══════════════════ Tarayıcı İndirme Yakalayıcı ═══════════════════
class BrowserDownloadWatcher:
    """
    Chrome/Edge indirme klasörünü izler.
    Yeni .crdownload dosyası algılandığında Chrome'un History DB'sinden
    URL'yi çekip callback ile bildirir.
    """

    # Desteklenen tarayıcı profil yolları
    BROWSER_PATHS = {
        "Chrome": os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data", "Default"),
        "Edge": os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "User Data", "Default"),
    }

    def __init__(self):
        self._running = False
        self._seen_files = set()       # Zaten gördüğümüz .crdownload dosyaları
        self._seen_urls = set()        # Zaten yakaladığımız URL'ler
        self._download_dir = None      # İzlenecek klasör
        self._browser_name = None
        self._history_path = None
        self.on_download_detected = None  # callback(url, filename, crdownload_path)
        self._detect_browser()

    def _detect_browser(self):
        """Chrome veya Edge'i bul."""
        for name, profile_dir in self.BROWSER_PATHS.items():
            history = os.path.join(profile_dir, "History")
            if os.path.exists(history):
                self._browser_name = name
                self._history_path = history
                # Tarayıcının default download dizinini bul
                prefs_path = os.path.join(profile_dir, "Preferences")
                if os.path.exists(prefs_path):
                    try:
                        with open(prefs_path, "r", encoding="utf-8") as f:
                            prefs = json.load(f)
                        dl_dir = prefs.get("download", {}).get("default_directory", "")
                        if dl_dir and os.path.isdir(dl_dir):
                            self._download_dir = dl_dir
                            return
                    except Exception:
                        pass
                # Varsayılan indirme klasörü
                self._download_dir = os.path.join(os.path.expanduser("~"), "Downloads")
                return
        # Hiçbir tarayıcı bulunamadı — yine de Downloads'u izle
        self._download_dir = os.path.join(os.path.expanduser("~"), "Downloads")

    def start(self, root):
        """İzlemeyi başlat. root = tkinter root widget (after için)."""
        if not self._download_dir:
            return
        self._running = True
        self._root = root
        # Başlangıçta mevcut .crdownload dosyalarını "görüldü" olarak işaretle
        self._scan_existing()
        self._poll()

    def _scan_existing(self):
        """Başlangıçta mevcut .crdownload dosyalarını kaydet (bunları yakalama)."""
        try:
            for f in os.listdir(self._download_dir):
                if f.endswith(".crdownload"):
                    self._seen_files.add(f)
        except Exception:
            pass

    def _poll(self):
        """Her 2 saniyede indirme klasörünü tara."""
        if not self._running:
            return
        try:
            for f in os.listdir(self._download_dir):
                if f.endswith(".crdownload") and f not in self._seen_files:
                    self._seen_files.add(f)
                    crdownload_path = os.path.join(self._download_dir, f)
                    # Dosya adını tahmin et (.crdownload kısmını çıkar)
                    orig_filename = f[:-11]  # ".crdownload" = 11 karakter
                    # Chrome History'den URL'yi çek (arka planda)
                    threading.Thread(
                        target=self._extract_url,
                        args=(orig_filename, crdownload_path),
                        daemon=True
                    ).start()
        except Exception:
            pass
        self._root.after(2000, self._poll)

    def _extract_url(self, filename, crdownload_path):
        """Chrome History DB'den indirme URL'sini çek."""
        if not self._history_path or not os.path.exists(self._history_path):
            return

        url = None
        temp_db = os.path.join(APP_DIR, "_history_tmp.db")

        try:
            # Chrome DB'yi kopyala (kilitli olduğu için doğrudan okunamaz)
            shutil.copy2(self._history_path, temp_db)

            with sqlite3.connect(temp_db) as conn:
                conn.row_factory = sqlite3.Row

                # Dosya adına göre son indirmeyi bul
                rows = conn.execute("""
                    SELECT d.id, d.target_path, d.tab_url, d.start_time,
                           d.total_bytes, d.mime_type
                    FROM downloads d
                    ORDER BY d.start_time DESC
                    LIMIT 10
                """).fetchall()

                target_id = None
                for row in rows:
                    target = row["target_path"] or ""
                    # Dosya adını karşılaştır
                    if filename and filename.lower() in target.lower():
                        target_id = row["id"]
                        break

                # İlk sonucu al (en son indirme)
                if target_id is None and rows:
                    target_id = rows[0]["id"]

                if target_id is not None:
                    # Gerçek indirme URL'sini downloads_url_chains'den çek
                    chain = conn.execute("""
                        SELECT url FROM downloads_url_chains
                        WHERE id = ?
                        ORDER BY chain_index DESC
                        LIMIT 1
                    """, (target_id,)).fetchone()

                    if chain:
                        url = chain["url"]

        except Exception:
            pass
        finally:
            try:
                if os.path.exists(temp_db):
                    os.remove(temp_db)
            except OSError:
                pass

        if url and url not in self._seen_urls:
            self._seen_urls.add(url)
            if self.on_download_detected:
                self._root.after(0, lambda u=url, fn=filename, p=crdownload_path:
                                 self.on_download_detected(u, fn, p))

    def stop(self):
        self._running = False

    def mark_url_seen(self, url):
        """URL'yi görüldü olarak işaretle."""
        self._seen_urls.add(url)

    @property
    def browser_name(self):
        return self._browser_name or "Tarayıcı"


# ═══════════════════ Video İndirici (yt-dlp) ═══════════════════
class VideoDownloader:
    @staticmethod
    def is_available():
        try:
            import yt_dlp
            return True
        except ImportError:
            return False

    @staticmethod
    def is_video_url(url: str) -> bool:
        """URL bir video platformundan mı? ClipboardWatcher ile aynı desenleri kullanır."""
        return ClipboardWatcher.is_video_url(url)

    @staticmethod
    def install():
        return True # Natively via requirements.txt

    @staticmethod
    def get_info(url):
        import yt_dlp
        opts = {'dump_single_json': True, 'quiet': True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                return ydl.extract_info(url, download=False)
            except Exception:
                return None

    @staticmethod
    def _has_ffmpeg():
        """FFmpeg kurulu mu kontrol et."""
        return shutil.which('ffmpeg') is not None

    @staticmethod
    def download(url, output_dir, format_id=None, on_progress=None, on_output=None, cancel_check=None):
        """
        yt-dlp ile video indirir.
        on_progress: callback(downloaded_bytes, total_bytes, speed_bps, eta_seconds, filename)
        cancel_check: callable -> bool (True ise indirme iptal edilir)
        Döner: (success: bool, output_path: str, title: str)
        """
        import yt_dlp

        downloaded_file = [None]
        video_title = ["Video"]

        class MyLogger:
            def debug(self, msg):
                if on_output and msg.strip() and not msg.startswith('[debug]'):
                    on_output(msg)
            def warning(self, msg):
                if on_output: on_output(f"Uyarı: {msg}")
            def error(self, msg):
                if on_output: on_output(f"Hata: {msg}")

        def progress_hook(d):
            if cancel_check and cancel_check():
                raise Exception("İndirme kullanıcı tarafından iptal edildi.")

            status = d.get('status')
            if status == 'downloading':
                dl = d.get('downloaded_bytes', 0)
                tot = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                spd = d.get('speed', 0) or 0
                eta = d.get('eta', 0) or 0
                fn = d.get('filename')
                if fn:
                    downloaded_file[0] = fn
                if on_progress:
                    on_progress(dl, tot, spd, eta, fn)
                if on_output:
                    p_str = d.get('_percent_str', '')
                    s_str = d.get('_speed_str', '')
                    e_str = d.get('_eta_str', '')
                    on_output(f"İndiriliyor: {p_str} (Hız: {s_str}, Kalan: {e_str})")
            elif status == 'finished':
                fn = d.get('filename')
                if fn:
                    downloaded_file[0] = fn
                if on_output:
                    on_output("İndirme tamamlandı, dosya işleniyor...")

        has_ffmpeg = VideoDownloader._has_ffmpeg()

        if format_id:
            fmt = format_id
        elif has_ffmpeg:
            fmt = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best'
        else:
            fmt = 'best[ext=mp4]/best[ext=webm]/best'

        opts = {
            'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
            'logger': MyLogger(),
            'progress_hooks': [progress_hook],
            'format': fmt,
            'no_warnings': False,
            'ignoreerrors': False,
            'retries': 3,
            'fragment_retries': 3,
        }

        if has_ffmpeg:
            opts['merge_output_format'] = 'mp4'
            opts['postprocessors'] = [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}]

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info:
                    video_title[0] = info.get('title', 'Video')
                    if not downloaded_file[0]:
                        try:
                            downloaded_file[0] = ydl.prepare_filename(info)
                        except Exception:
                            pass
            out_p = downloaded_file[0] or output_dir
            return True, out_p, video_title[0]
        except Exception as e:
            err_str = str(e)
            if on_output:
                on_output(f"Hata: {err_str}")
            if "iptal" in err_str.lower():
                return False, None, "İptal Edildi"
            # FFmpeg hatasında birleşik format ile tekrar dene
            if has_ffmpeg and ('ffmpeg' in err_str.lower() or 'merge' in err_str.lower()):
                if on_output:
                    on_output("FFmpeg sorunu algılandı, doğrudan format ile deneniyor...")
                opts2 = {
                    'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
                    'logger': MyLogger(),
                    'progress_hooks': [progress_hook],
                    'format': 'best[ext=mp4]/best[ext=webm]/best',
                    'retries': 3,
                }
                try:
                    with yt_dlp.YoutubeDL(opts2) as ydl:
                        info = ydl.extract_info(url, download=True)
                        if info:
                            video_title[0] = info.get('title', 'Video')
                            downloaded_file[0] = ydl.prepare_filename(info)
                    return True, downloaded_file[0] or output_dir, video_title[0]
                except Exception as e2:
                    if on_output:
                        on_output(f"İkinci deneme hatası: {e2}")
            return False, None, None


# ═══════════════════ Torrent İndirici (torrentp) ═══════════════════
class TorrentDownloader:
    @staticmethod
    def is_magnet_or_torrent(url: str) -> bool:
        u = url.strip()
        return u.startswith("magnet:?") or u.endswith(".torrent")

    @staticmethod
    def is_available() -> bool:
        try:
            import torrentp
            import libtorrent
            return True
        except Exception:
            return False

    @staticmethod
    def download(url: str, output_dir: str, on_progress=None, cancel_check=None):
        """
        Torrent veya Magnet linkini arka planda asenkron olarak indirir.
        on_progress: callback(name, downloaded, total, speed, peers, state_str)
        cancel_check: callable -> bool (True ise iptal)
        Döner: (success: bool, output_path: str, name: str)
        """
        if not TorrentDownloader.is_available():
            if on_progress:
                on_progress("Torrent", 0, 0, 0, 0, "Hata: Torrent kütüphanesi hazır değil")
            return False, None, "Torrent Hatası"

        import torrentp
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        dl = torrentp.TorrentDownloader(url, output_dir)
        info_holder = {"name": "Torrent Dosyası", "success": False}

        async def _monitor():
            while True:
                if cancel_check and cancel_check():
                    try:
                        dl.stop_download()
                    except Exception:
                        pass
                    break

                if dl._downloader:
                    try:
                        st = dl._downloader.status()
                        if st:
                            name = getattr(st, 'name', '') or info_holder["name"]
                            info_holder["name"] = name
                            done = getattr(st, 'total_done', 0)
                            total = getattr(st, 'total_wanted', 0)
                            rate = getattr(st, 'download_rate', 0)
                            peers = getattr(st, 'num_peers', 0)
                            state = str(getattr(st, 'state', ''))
                            is_seeding = getattr(st, 'is_seeding', False)

                            if on_progress:
                                on_progress(name, done, total, rate, peers, state)

                            if is_seeding or (total > 0 and done >= total):
                                info_holder["success"] = True
                                break
                    except Exception:
                        pass
                await asyncio.sleep(0.5)

        async def _run():
            t_dl = asyncio.create_task(dl.start_download())
            t_mon = asyncio.create_task(_monitor())
            done, pending = await asyncio.wait([t_dl, t_mon], return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()

        try:
            loop.run_until_complete(_run())
            info_holder["success"] = True
        except Exception as e:
            if on_progress:
                on_progress(info_holder["name"], 0, 0, 0, 0, f"Hata: {e}")
        finally:
            try:
                dl.stop_download()
            except Exception:
                pass
            loop.close()

        final_path = os.path.join(output_dir, info_holder["name"])
        return info_holder["success"], final_path, info_holder["name"]


# ═══════════════════ Zamanlayıcı ═══════════════════
class DownloadScheduler:
    """Kalıcı zamanlanmış indirme yöneticisi."""

    def __init__(self):
        self._timers = {}
        self._items = []
        self.on_trigger = None
        self._file = os.path.join(APP_DIR, "schedules.json")
        self._load()

    def _load(self):
        if os.path.exists(self._file):
            try:
                with open(self._file, "r", encoding="utf-8") as f:
                    self._items = json.load(f)
                # Gelecekteki zamanlamaları tekrar kur
                now = datetime.now()
                for item in list(self._items):
                    if item.get("status") == "scheduled":
                        try:
                            t = datetime.strptime(item["time"], "%Y-%m-%d %H:%M")
                            delay = (t - now).total_seconds()
                            if delay > 0:
                                timer = threading.Timer(delay, self._fire, args=(item,))
                                timer.daemon = True
                                timer.start()
                                self._timers[item["id"]] = timer
                            else:
                                item["status"] = "expired"
                        except Exception:
                            item["status"] = "expired"
            except Exception:
                self._items = []

    def _save(self):
        try:
            with open(self._file, "w", encoding="utf-8") as f:
                json.dump(self._items, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def schedule(self, url: str, time_str: str, connections: int = 16):
        sid = str(int(time.time() * 1000))
        now = datetime.now()
        try:
            if len(time_str) <= 5:
                t = datetime.strptime(time_str, "%H:%M").replace(year=now.year, month=now.month, day=now.day)
                if t <= now:
                    t += timedelta(days=1)
            else:
                t = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
        except ValueError:
            return None

        delay = (t - now).total_seconds()
        if delay <= 0:
            return None

        item = {
            "id": sid,
            "url": url,
            "connections": connections,
            "time": t.strftime("%Y-%m-%d %H:%M"),
            "status": "scheduled",
        }
        self._items.append(item)
        self._save()

        timer = threading.Timer(delay, self._fire, args=(item,))
        timer.daemon = True
        timer.start()
        self._timers[sid] = timer
        return item

    def _fire(self, item):
        item["status"] = "triggered"
        self._save()
        if self.on_trigger:
            self.on_trigger(item)
        self._timers.pop(item["id"], None)

    def cancel(self, sid):
        t = self._timers.pop(sid, None)
        if t:
            t.cancel()
        self._items = [i for i in self._items if i["id"] != sid]
        self._save()

    def get_all(self):
        return [i for i in self._items if i["status"] == "scheduled"]

    def stop_all(self):
        for t in self._timers.values():
            t.cancel()
        self._timers.clear()
