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
        fn = os.path.basename(filepath)
        cat = FileCategorizer.get_category(fn, categories)
        dest_dir = os.path.join(base_dir, cat)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, fn)
        if os.path.exists(dest):
            name, ext = os.path.splitext(fn)
            dest = os.path.join(dest_dir, f"{name}_{int(time.time())}{ext}")
        shutil.move(filepath, dest)
        return dest


# ═══════════════════ Otomatik Arşiv Açma ═══════════════════
class AutoExtractor:
    SUPPORTED = {".zip", ".tar", ".gz", ".bz2", ".tgz", ".tar.gz", ".tar.bz2"}

    @staticmethod
    def can_extract(filename):
        return any(filename.lower().endswith(e) for e in AutoExtractor.SUPPORTED)

    @staticmethod
    def extract(filepath, dest=None):
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
    def download(url, output_dir, format_id=None, on_output=None):
        import yt_dlp

        class MyLogger:
            def debug(self, msg):
                if on_output: on_output(msg)
            def warning(self, msg):
                if on_output: on_output(msg)
            def error(self, msg):
                if on_output: on_output(msg)

        def progress_hook(d):
            if d['status'] == 'downloading':
                percent = d.get('_percent_str', '0%')
                speed = d.get('_speed_str', '0B/s')
                eta = d.get('_eta_str', 'Unknown ETA')
                if on_output:
                    on_output(f"İndiriliyor: {percent} (Hız: {speed}, Süre: {eta})")
            elif d['status'] == 'finished':
                if on_output:
                    on_output("İndirme bitti, birleştiriliyor...")

        opts = {
            'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
            'logger': MyLogger(),
            'progress_hooks': [progress_hook],
        }
        if format_id:
            opts['format'] = format_id

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            return True
        except Exception as e:
            if on_output: on_output(f"Hata: {str(e)}")
            return False

# ═══════════════════ Torrent İndirici (torrentp) ═══════════════════
class TorrentDownloader:
    @staticmethod
    def is_magnet_or_torrent(url):
        url = url.strip()
        return url.startswith("magnet:?") or url.endswith(".torrent")

    @staticmethod
    def download(url, output_dir, on_progress=None):
        import torrentp
        import asyncio

        # torrentp is blocking start_download, runs its own loop or asyncio
        # We wrap in thread
        success = False
        try:
            dl = torrentp.TorrentDownloader(url, output_dir)
            if on_progress:
                on_progress("Torrent başlatılıyor (Metadata/Peers aranıyor)...")
            # We can't simply hook streamly, so we just run it blockingly
            dl.start_download()
            success = True
        except Exception as e:
            if on_progress:
                on_progress(f"Torrent hatası: {e}")
        return success


# ═══════════════════ Zamanlayıcı ═══════════════════
class DownloadScheduler:
    def __init__(self):
        self._timers = {}
        self._items = []
        self.on_trigger = None

    def schedule(self, url, time_str, connections=16):
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

        item = {"id": sid, "url": url, "connections": connections,
                "time": t.strftime("%Y-%m-%d %H:%M"), "status": "scheduled"}
        self._items.append(item)

        timer = threading.Timer(delay, self._fire, args=(item,))
        timer.daemon = True
        timer.start()
        self._timers[sid] = timer
        return item

    def _fire(self, item):
        item["status"] = "triggered"
        if self.on_trigger:
            self.on_trigger(item)
        self._timers.pop(item["id"], None)

    def cancel(self, sid):
        t = self._timers.pop(sid, None)
        if t:
            t.cancel()
        self._items = [i for i in self._items if i["id"] != sid]

    def get_all(self):
        return [i for i in self._items if i["status"] == "scheduled"]

    def stop_all(self):
        for t in self._timers.values():
            t.cancel()
        self._timers.clear()
