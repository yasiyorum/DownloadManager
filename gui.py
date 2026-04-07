"""
Download Manager — Modern CustomTkinter GUI v3
───────────────────────────────────────────────
• Kalıcı duraklatma/devam + tarayıcı yakalama
• İndirme kuyruğu + zamanlayıcı
• Geçmiş, toplu indirme, video indirici
• Hız sınırlama, otomatik arşiv açma, kategorilendirme
• System tray + bildirimler + otomatik başlangıç
"""

import asyncio, os, threading, uuid
from tkinter import filedialog, messagebox

import customtkinter as ctk
from download_engine import DownloadEngine
from features import (
    SettingsManager, DownloadHistory, FileCategorizer,
    AutoExtractor, Notifications, DownloadScheduler,
    TorrentDownloader,
)
from dialogs import (
    SettingsDialog, HistoryDialog, BatchDialog,
    VideoDialog, ScheduleDialog,
)

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

# ──────────────── Tema ────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BG_DARK = "#0d1117"; BG_CARD = "#161b22"; BG_INPUT = "#1c2128"
BORDER_COLOR = "#30363d"; ACCENT = "#1f6feb"; ACCENT_HOVER = "#388bfd"
GREEN = "#2ea043"; RED = "#da3633"; ORANGE = "#d29922"
TEXT_PRIMARY = "#e6edf3"; TEXT_SECONDARY = "#8b949e"; TEXT_DIM = "#484f58"
FONT_FAMILY = "Segoe UI"


class ChunkRow(ctk.CTkFrame):
    STATUS_COLORS = {"pending": TEXT_DIM, "downloading": ACCENT,
                     "completed": GREEN, "failed": RED, "retrying": ORANGE}
    STATUS_LABELS = {"pending": "Bekliyor", "downloading": "İndiriliyor",
                     "completed": "Tamamlandı", "failed": "Başarısız", "retrying": "Yeniden..."}

    def __init__(self, master, idx, size, **kw):
        super().__init__(master, fg_color="transparent", height=28, **kw)
        self.grid_columnconfigure(1, weight=1)
        self.idx_label = ctk.CTkLabel(self, text=f"#{idx+1:03d}", font=(FONT_FAMILY, 11),
                                       text_color=TEXT_SECONDARY, width=50, anchor="w")
        self.idx_label.grid(row=0, column=0, padx=(8, 4), pady=1)
        self.progress = ctk.CTkProgressBar(self, height=8, corner_radius=4,
                                            fg_color=BG_INPUT, progress_color=ACCENT)
        self.progress.grid(row=0, column=1, sticky="ew", padx=4, pady=1)
        self.progress.set(0)
        self.status_label = ctk.CTkLabel(self, text="Bekliyor", font=(FONT_FAMILY, 11),
                                          text_color=TEXT_DIM, width=90, anchor="e")
        self.status_label.grid(row=0, column=2, padx=(4, 8), pady=1)

    def update_status(self, status, downloaded, size):
        color = self.STATUS_COLORS.get(status, TEXT_DIM)
        label = self.STATUS_LABELS.get(status, status)
        pct = downloaded / size if size > 0 else 0
        self.progress.set(min(pct, 1.0))
        pc = {GREEN: "completed", RED: "failed", ORANGE: "retrying"}.get(
            self.STATUS_COLORS.get(status), None)
        self.progress.configure(progress_color=self.STATUS_COLORS.get(status, ACCENT))
        self.status_label.configure(text=label, text_color=color)


class QueueItemRow(ctk.CTkFrame):
    STATUS_MAP = {"pending": ("⏳", TEXT_DIM), "downloading": ("▶", ACCENT),
                  "completed": ("✅", GREEN), "failed": ("❌", RED)}

    def __init__(self, master, item, on_remove=None, **kw):
        super().__init__(master, fg_color=BG_INPUT, corner_radius=8, height=36, **kw)
        self.grid_columnconfigure(1, weight=1)
        icon, color = self.STATUS_MAP.get(item["status"], ("?", TEXT_DIM))
        ctk.CTkLabel(self, text=icon, font=(FONT_FAMILY, 13), width=28
                     ).grid(row=0, column=0, padx=(8, 2), pady=4)
        ctk.CTkLabel(self, text=item.get("filename", "?"), font=(FONT_FAMILY, 12),
                     text_color=TEXT_PRIMARY, anchor="w"
                     ).grid(row=0, column=1, padx=4, pady=4, sticky="ew")
        sz = item.get("file_size", 0)
        ctk.CTkLabel(self, text=DownloadEngine._format_size(sz) if sz > 0 else "?",
                     font=(FONT_FAMILY, 11), text_color=TEXT_DIM, width=70, anchor="e"
                     ).grid(row=0, column=2, padx=4, pady=4)
        if item["status"] == "pending" and on_remove:
            ctk.CTkButton(self, text="✖", width=28, height=28, font=(FONT_FAMILY, 12),
                           fg_color="transparent", hover_color=RED, text_color=TEXT_DIM,
                           command=lambda: on_remove(item["id"])
                           ).grid(row=0, column=3, padx=(0, 6), pady=4)


# ═══════════════════ Ana Uygulama ═══════════════════
class DownloadManagerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("⚡ Agresif İndirme Yöneticisi")
        self.geometry("780x820"); self.minsize(680, 700)
        self.configure(fg_color=BG_DARK)

        # Modüller
        self.settings = SettingsManager()
        self.history = DownloadHistory()
        self.scheduler = DownloadScheduler()
        self.scheduler.on_trigger = self._on_scheduled_trigger
        self.engine = DownloadEngine()
        self._loop = None; self._thread = None
        self._download_running = False; self._chunk_rows = []
        self._pending_resume = None; self._current_queue_item = None
        self._save_dir = self.settings.get("save_dir")
        self._download_queue = []
        self._tray_icon = None

        self._build_ui()
        if HAS_TRAY: self._setup_tray()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(500, self._check_pending_downloads)

    def _build_ui(self):
        self.grid_rowconfigure(5, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # ── Header ──
        header = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=56)
        header.grid(row=0, column=0, sticky="ew"); header.grid_columnconfigure(1, weight=1)
        header.grid_propagate(False)
        ctk.CTkLabel(header, text="⚡  Agresif İndirme Yöneticisi",
                     font=(FONT_FAMILY, 18, "bold"), text_color=TEXT_PRIMARY
                     ).grid(row=0, column=0, padx=20, pady=14, sticky="w")
        ctk.CTkLabel(header, text="v3.0", font=(FONT_FAMILY, 12), text_color=TEXT_DIM
                     ).grid(row=0, column=1, padx=20, pady=14, sticky="e")

        # ── Araç Çubuğu ──
        toolbar = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=10, height=42)
        toolbar.grid(row=1, column=0, padx=16, pady=(8, 4), sticky="ew")
        toolbar.grid_propagate(False)
        tbtns = [
            ("📊 Geçmiş", self._show_history), ("📋 Toplu", self._show_batch),
            ("🎬 Video", self._show_video), ("⏰ Zamanlı", self._show_schedule),
            ("⚙ Ayarlar", self._show_settings),
        ]
        for i, (txt, cmd) in enumerate(tbtns):
            ctk.CTkButton(toolbar, text=txt, width=100, height=32, font=(FONT_FAMILY, 11),
                           fg_color="transparent", hover_color=ACCENT, text_color=TEXT_SECONDARY,
                           command=cmd).pack(side="left", padx=2, pady=5)

        # ── URL Girişi ──
        url_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12)
        url_frame.grid(row=2, column=0, padx=16, pady=(6, 4), sticky="ew")
        url_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(url_frame, text="URL", font=(FONT_FAMILY, 13, "bold"),
                     text_color=TEXT_SECONDARY).grid(row=0, column=0, padx=(16, 8), pady=(14, 4), sticky="w")
        self.url_entry = ctk.CTkEntry(url_frame, placeholder_text="İndirme bağlantısını yapıştırın...",
                                       font=(FONT_FAMILY, 13), height=40,
                                       fg_color=BG_INPUT, border_color=BORDER_COLOR, text_color=TEXT_PRIMARY)
        self.url_entry.grid(row=0, column=1, padx=(0, 8), pady=(14, 4), sticky="ew")
        ctk.CTkButton(url_frame, text="📋", width=42, height=40, font=(FONT_FAMILY, 16),
                       fg_color=BG_INPUT, border_color=BORDER_COLOR, border_width=1,
                       hover_color=ACCENT, command=self._paste_url
                       ).grid(row=0, column=2, padx=(0, 16), pady=(14, 4))

        # Bağlantı slider + hız göstergesi
        ctk.CTkLabel(url_frame, text="Bağlantı", font=(FONT_FAMILY, 12),
                     text_color=TEXT_SECONDARY).grid(row=1, column=0, padx=(16, 8), pady=(4, 14), sticky="w")
        slider_box = ctk.CTkFrame(url_frame, fg_color="transparent")
        slider_box.grid(row=1, column=1, padx=(0, 8), pady=(4, 14), sticky="ew")
        slider_box.grid_columnconfigure(0, weight=1)
        self.conn_slider = ctk.CTkSlider(slider_box, from_=1, to=64, number_of_steps=63,
                                          fg_color=BG_INPUT, progress_color=ACCENT,
                                          button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                                          command=self._on_slider_change)
        self.conn_slider.set(self.settings.get("default_connections", 16))
        self.conn_slider.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.conn_label = ctk.CTkLabel(slider_box, text=str(self.settings.get("default_connections", 16)),
                                        font=(FONT_FAMILY, 13, "bold"), text_color=ACCENT, width=36)
        self.conn_label.grid(row=0, column=1)

        # Hız sınırı göstergesi
        speed_limit = self.settings.get("speed_limit", 0)
        sl_txt = f"🚀 {DownloadEngine._format_speed(speed_limit)}" if speed_limit > 0 else "🚀 Sınırsız"
        self.speed_limit_label = ctk.CTkLabel(slider_box, text=sl_txt, font=(FONT_FAMILY, 10),
                                               text_color=TEXT_DIM, width=80)
        self.speed_limit_label.grid(row=0, column=2, padx=(8, 0))

        ctk.CTkButton(url_frame, text="📁 Kayıt Yolu", width=120, height=32,
                       font=(FONT_FAMILY, 12), fg_color=BG_INPUT, border_color=BORDER_COLOR,
                       border_width=1, hover_color=ACCENT, command=self._choose_folder
                       ).grid(row=1, column=2, padx=(0, 16), pady=(4, 14))

        # ── Butonlar ──
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=3, column=0, padx=16, pady=4, sticky="ew")
        btn_frame.grid_columnconfigure((0, 1, 2), weight=1)
        self.dl_btn = ctk.CTkButton(btn_frame, text="⬇  İNDİR", height=44,
                                     font=(FONT_FAMILY, 14, "bold"), fg_color=ACCENT,
                                     hover_color=ACCENT_HOVER, command=self._start_download)
        self.dl_btn.grid(row=0, column=0, padx=(0, 4), sticky="ew")
        self.pause_btn = ctk.CTkButton(btn_frame, text="⏸  Duraklat", height=44,
                                        font=(FONT_FAMILY, 14, "bold"), fg_color=ORANGE,
                                        hover_color="#e3a826", state="disabled", command=self._toggle_pause)
        self.pause_btn.grid(row=0, column=1, padx=4, sticky="ew")
        self.cancel_btn = ctk.CTkButton(btn_frame, text="✖  İptal + Sil", height=44,
                                         font=(FONT_FAMILY, 14, "bold"), fg_color=RED,
                                         hover_color="#f85149", state="disabled", command=self._cancel_download)
        self.cancel_btn.grid(row=0, column=2, padx=(4, 0), sticky="ew")

        # ── Kuyruk ──
        queue_card = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12)
        queue_card.grid(row=4, column=0, padx=16, pady=4, sticky="ew")
        queue_card.grid_columnconfigure(0, weight=1)
        qh = ctk.CTkFrame(queue_card, fg_color="transparent")
        qh.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 4))
        qh.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(qh, text="📋 İndirme Kuyruğu", font=(FONT_FAMILY, 13, "bold"),
                     text_color=TEXT_PRIMARY).grid(row=0, column=0, sticky="w")
        self.queue_count_label = ctk.CTkLabel(qh, text="Boş", font=(FONT_FAMILY, 11), text_color=TEXT_DIM)
        self.queue_count_label.grid(row=0, column=1, sticky="e")
        self.queue_scroll = ctk.CTkScrollableFrame(queue_card, fg_color=BG_DARK, corner_radius=8,
                                                    height=60, scrollbar_button_color=BORDER_COLOR,
                                                    scrollbar_button_hover_color=TEXT_DIM)
        self.queue_scroll.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
        self.queue_scroll.grid_columnconfigure(0, weight=1)
        self._queue_empty = ctk.CTkLabel(self.queue_scroll, text="Kuyruk boş — tarayıcıdan veya URL ile indirme ekleyin",
                                          font=(FONT_FAMILY, 11), text_color=TEXT_DIM)
        self._queue_empty.grid(row=0, column=0, pady=6)

        # ── Progress ──
        pc = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12)
        pc.grid(row=5, column=0, padx=16, pady=4, sticky="nsew")
        pc.grid_rowconfigure(3, weight=1); pc.grid_columnconfigure(0, weight=1)
        pt = ctk.CTkFrame(pc, fg_color="transparent")
        pt.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4)); pt.grid_columnconfigure(0, weight=1)
        self.pct_label = ctk.CTkLabel(pt, text="0%", font=(FONT_FAMILY, 22, "bold"), text_color=TEXT_PRIMARY)
        self.pct_label.grid(row=0, column=0, sticky="w")
        esf = ctk.CTkFrame(pt, fg_color="transparent"); esf.grid(row=0, column=1, sticky="e")
        self.eta_label = ctk.CTkLabel(esf, text="Kalan: --", font=(FONT_FAMILY, 13),
                                       text_color=ACCENT, width=140, anchor="e")
        self.eta_label.grid(row=0, column=0, padx=(0, 12))
        self.speed_label = ctk.CTkLabel(esf, text="— MB/s", font=(FONT_FAMILY, 13), text_color=TEXT_SECONDARY)
        self.speed_label.grid(row=0, column=1)
        self.main_progress = ctk.CTkProgressBar(pc, height=14, corner_radius=7,
                                                 fg_color=BG_INPUT, progress_color=ACCENT)
        self.main_progress.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 4)); self.main_progress.set(0)
        ir = ctk.CTkFrame(pc, fg_color="transparent")
        ir.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8)); ir.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(ir, text="Hazır", font=(FONT_FAMILY, 12),
                                          text_color=TEXT_SECONDARY, anchor="w")
        self.status_label.grid(row=0, column=0, sticky="w")
        self.size_label = ctk.CTkLabel(ir, text="", font=(FONT_FAMILY, 12),
                                        text_color=TEXT_SECONDARY, anchor="e")
        self.size_label.grid(row=0, column=1, sticky="e")
        self.chunk_scroll = ctk.CTkScrollableFrame(pc, fg_color=BG_DARK, corner_radius=8,
                                                    scrollbar_button_color=BORDER_COLOR,
                                                    scrollbar_button_hover_color=TEXT_DIM)
        self.chunk_scroll.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.chunk_scroll.grid_columnconfigure(0, weight=1)

        # ── Footer ──
        footer = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=32)
        footer.grid(row=6, column=0, sticky="ew"); footer.grid_propagate(False)
        footer.grid_columnconfigure(0, weight=1)
        self.footer_label = ctk.CTkLabel(footer, text=f"Kayıt: {self._save_dir}",
                                          font=(FONT_FAMILY, 11), text_color=TEXT_DIM)
        self.footer_label.grid(row=0, column=0, padx=16, pady=6, sticky="w")

    # ═══════════════ Toolbar Actions ═══════════════
    def _show_settings(self):
        SettingsDialog(self, self.settings)
        self._apply_settings()

    def _show_history(self):
        HistoryDialog(self, self.history)

    def _show_batch(self):
        BatchDialog(self, self._add_url_to_queue)

    def _show_video(self):
        VideoDialog(self, self._save_dir)

    def _show_schedule(self):
        ScheduleDialog(self, self.scheduler)



    def _apply_settings(self):
        self._save_dir = self.settings.get("save_dir")
        self.footer_label.configure(text=f"Kayıt: {self._save_dir}")
        sl = self.settings.get("speed_limit", 0)
        self.speed_limit_label.configure(
            text=f"🚀 {DownloadEngine._format_speed(sl)}" if sl > 0 else "🚀 Sınırsız")
        dc = self.settings.get("default_connections", 16)
        self.conn_slider.set(dc); self.conn_label.configure(text=str(dc))

    def _add_url_to_queue(self, url):
        item = {"id": str(uuid.uuid4()), "url": url, "filename": url.split("/")[-1][:40] or "download",
                "file_size": 0, "connections": int(self.conn_slider.get()), "status": "pending"}
        self._download_queue.append(item)
        self._refresh_queue_ui()
        self._process_queue()

    # ═══════════════ Queue ═══════════════
    def _refresh_queue_ui(self):
        for w in self.queue_scroll.winfo_children():
            w.destroy()
        if not self._download_queue:
            ctk.CTkLabel(self.queue_scroll, text="Kuyruk boş",
                         font=(FONT_FAMILY, 11), text_color=TEXT_DIM).grid(row=0, column=0, pady=6)
            self.queue_count_label.configure(text="Boş")
            return
        for i, item in enumerate(self._download_queue):
            QueueItemRow(self.queue_scroll, item, on_remove=self._remove_from_queue
                         ).grid(row=i, column=0, sticky="ew", pady=2)
        p = sum(1 for q in self._download_queue if q["status"] == "pending")
        a = sum(1 for q in self._download_queue if q["status"] == "downloading")
        d = sum(1 for q in self._download_queue if q["status"] == "completed")
        self.queue_count_label.configure(text=f"{p} sırada · {a} aktif · {d} tamamlandı")

    def _remove_from_queue(self, item_id):
        self._download_queue = [q for q in self._download_queue if q["id"] != item_id]
        self._refresh_queue_ui()

    def _process_queue(self):
        if self._download_running:
            return
        for item in self._download_queue:
            if item["status"] == "pending":
                item["status"] = "downloading"
                self._refresh_queue_ui()
                self.url_entry.delete(0, "end"); self.url_entry.insert(0, item["url"])
                self.conn_slider.set(item["connections"])
                self.conn_label.configure(text=str(item["connections"]))
                self._start_download(queue_item=item)
                return

    def _on_scheduled_trigger(self, item):
        self.after(0, lambda: self._add_url_to_queue(item["url"]))

    # ═══════════════ Tray ═══════════════
    def _setup_tray(self):
        try:
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.rounded_rectangle([(2, 2), (62, 62)], radius=12, fill=(31, 111, 235, 255))
            d.polygon([(34, 10), (22, 30), (30, 30), (24, 54), (42, 28), (34, 28), (40, 10)],
                      fill=(255, 255, 255, 255))
            menu = pystray.Menu(pystray.MenuItem("Göster", self._tray_show, default=True),
                                pystray.Menu.SEPARATOR, pystray.MenuItem("Çıkış", self._tray_quit))
            self._tray_icon = pystray.Icon("agresif_dm", img, "Agresif İndirme Yöneticisi", menu)
            threading.Thread(target=self._tray_icon.run, daemon=True).start()
        except Exception:
            self._tray_icon = None

    def _tray_show(self, *a): self.after(0, self.deiconify); self.after(10, self.lift)
    def _tray_quit(self, *a):
        if self._tray_icon: self._tray_icon.stop()
        self.after(0, self._force_quit)

    def _on_close(self):
        if HAS_TRAY and self._tray_icon: self.withdraw()
        else: self._force_quit()

    def _force_quit(self):
        if self._download_running and self.engine:
            self.engine.cancel()
            if self._thread and self._thread.is_alive(): self._thread.join(timeout=3)
        self.scheduler.stop_all()
        self.destroy()

    # ═══════════════ Pending Downloads ═══════════════
    def _check_pending_downloads(self):
        pending = DownloadEngine.find_pending_downloads(self._save_dir)
        if not pending: return
        latest = max(pending, key=lambda p: p["data"].get("timestamp", 0))
        data = latest["data"]; fn = data["filename"]
        dl = latest["downloaded_bytes"]; total = data["total_size"]
        pct = (dl / total * 100) if total > 0 else 0
        if messagebox.askyesno("Yarım Kalan İndirme",
            f"Dosya: {fn}\nİlerleme: {pct:.1f}%\nDevam etmek ister misiniz?"):
            self._pending_resume = latest
            self.url_entry.delete(0, "end"); self.url_entry.insert(0, data["url"])
            self.conn_slider.set(data["connections"]); self.conn_label.configure(text=str(data["connections"]))
            self._start_download(resume=True)

    # ═══════════════ Actions ═══════════════
    def _paste_url(self):
        try:
            t = self.clipboard_get(); self.url_entry.delete(0, "end"); self.url_entry.insert(0, t.strip())
        except Exception: pass

    def _on_slider_change(self, v): self.conn_label.configure(text=str(int(v)))

    def _choose_folder(self):
        d = filedialog.askdirectory(initialdir=self._save_dir)
        if d:
            self._save_dir = d; self.settings.set("save_dir", d)
            self.footer_label.configure(text=f"Kayıt: {self._save_dir}")

    def _start_download(self, resume=False, queue_item=None):
        url = self.url_entry.get().strip()
        if not url and not resume:
            self.status_label.configure(text="⚠ Lütfen bir URL girin.", text_color=ORANGE); return
        connections = int(self.conn_slider.get())
        self._download_running = True; self._current_queue_item = queue_item
        self._set_ui_state(downloading=True)
        for w in self.chunk_scroll.winfo_children(): w.destroy()
        self._chunk_rows = []; self.main_progress.set(0)
        self.main_progress.configure(progress_color=ACCENT)
        self.pct_label.configure(text="0%", text_color=TEXT_PRIMARY)
        self.speed_label.configure(text="— MB/s"); self.eta_label.configure(text="Kalan: --")
        self.size_label.configure(text="")
        self.status_label.configure(
            text="Kaldığı yerden devam ediliyor..." if resume else "Başlatılıyor...",
            text_color=ACCENT if resume else TEXT_SECONDARY)

        self.engine = DownloadEngine()
        self.engine.speed_limit = self.settings.get("speed_limit", 0)
        self.engine.on_progress = self._cb_progress
        self.engine.on_chunk_update = self._cb_chunk
        self.engine.on_status = self._cb_status
        self.engine.on_complete = self._cb_complete
        self.engine.on_error = self._cb_error

        # Torrent kontrol
        if TorrentDownloader.is_magnet_or_torrent(url):
            self._thread = threading.Thread(target=self._run_torrent, args=(url, self._save_dir), daemon=True)
            self._thread.start()
            return

        resume_state = self._pending_resume if resume else None
        self._pending_resume = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop,
                                         args=(url, connections, self._save_dir, resume_state), daemon=True)
        self._thread.start()

    def _run_torrent(self, url, save_dir):
        def _on_prog(msg):
            self.after(0, lambda: self.status_label.configure(text=msg, text_color=ACCENT))
        
        ok = TorrentDownloader.download(url, save_dir, on_progress=_on_prog)
        self._download_running = False
        if ok:
            self.after(0, lambda: self.status_label.configure(text="✅ Torrent tamamlandı!", text_color=GREEN))
            self.engine._state_meta = {
                "output_path": os.path.join(save_dir, "Torrent Download"),
                "filename": "Torrent Files",
                "total_size": 0, "url": url
            }
        else:
            self.after(0, lambda: self.status_label.configure(text="❌ Torrent hatası!", text_color=RED))
            self.status_label.configure(text="❌ Torrent başarısız!")
            pass
        self.after(0, self._on_download_finished)

    def _run_loop(self, url, connections, save_dir, resume_state):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self.engine.start(url, connections, save_dir, resume_state))
        self._download_running = False
        self.after(0, self._on_download_finished)

    def _on_download_finished(self):
        self._set_ui_state(downloading=False)
        st = self.status_label.cget("text") if self.status_label else ""
        completed = "✅" in st or "Tamamlandı" in st

        if completed:
            # Post-download işlemleri
            if hasattr(self.engine, "_state_meta") and self.engine._state_meta:
                out = self.engine._state_meta.get("output_path", "")
                fn = self.engine._state_meta.get("filename", "")
                sz = self.engine._state_meta.get("total_size", 0)
                url = self.engine._state_meta.get("url", "")

                # Geçmişe ekle
                self.history.add(url, fn, sz, "completed", out)

                # Otomatik arşiv açma
                if self.settings.get("auto_extract") and out and AutoExtractor.can_extract(fn):
                    result = AutoExtractor.extract(out)
                    if result:
                        self.status_label.configure(text=f"✅ Tamamlandı + Arşiv açıldı: {os.path.basename(result)}")

                # Otomatik kategorilendirme
                if self.settings.get("auto_categorize") and out and os.path.exists(out):
                    cats = self.settings.get("categories", {})
                    new_path = FileCategorizer.move_to_category(out, self._save_dir, cats)

                # Bildirim
                if self.settings.get("notify_on_complete"):
                    Notifications.show("İndirme Tamamlandı", f"{fn} başarıyla indirildi!")

        if self._current_queue_item:
            self._current_queue_item["status"] = "completed" if completed else "failed"
            self._current_queue_item = None
            self._refresh_queue_ui()
        self.after(1000, self._process_queue)

    def _toggle_pause(self):
        if self.engine.is_paused:
            self.engine.resume(); self.pause_btn.configure(text="⏸  Duraklat")
            self.status_label.configure(text="Devam ediliyor...", text_color=TEXT_SECONDARY)
        else:
            self.engine.pause(); self.pause_btn.configure(text="▶  Devam")
            self.status_label.configure(text="Duraklatıldı", text_color=ORANGE)

    def _cancel_download(self):
        if not messagebox.askyesno("İptal", "İndirme iptal edilecek. Emin misiniz?"): return
        self.engine.cancel()
        if hasattr(self.engine, '_state') and self.engine._state:
            if hasattr(self.engine, '_state_meta'):
                td = self.engine._state_meta.get("temp_dir", "")
                if td and os.path.isdir(td):
                    import shutil
                    try: shutil.rmtree(td)
                    except OSError: pass
            self.engine._state.delete()
        self.status_label.configure(text="❌ İptal edildi.", text_color=RED)
        if self._current_queue_item:
            self._current_queue_item["status"] = "failed"
            self._current_queue_item = None; self._refresh_queue_ui()

    # ═══════════════ Callbacks ═══════════════
    def _cb_progress(self, downloaded, total, speed):
        def _u():
            if total > 0:
                p = downloaded / total; self.main_progress.set(min(p, 1.0))
                self.pct_label.configure(text=f"{p*100:.1f}%")
                self.size_label.configure(text=f"{DownloadEngine._format_size(downloaded)} / {DownloadEngine._format_size(total)}")
                rem = total - downloaded
                if speed > 0 and rem > 0:
                    self.eta_label.configure(text=f"Kalan: {DownloadEngine._format_time(rem/speed)}", text_color=ACCENT)
                elif rem <= 0:
                    self.eta_label.configure(text="Kalan: 0s", text_color=GREEN)
                else:
                    self.eta_label.configure(text="Kalan: hesaplanıyor...", text_color=TEXT_DIM)
            self.speed_label.configure(text=DownloadEngine._format_speed(speed))
        self.after(0, _u)

    def _cb_chunk(self, idx, status, downloaded, size):
        def _u():
            while len(self._chunk_rows) <= idx:
                r = ChunkRow(self.chunk_scroll, len(self._chunk_rows), size)
                r.grid(row=len(self._chunk_rows), column=0, sticky="ew", pady=1)
                self._chunk_rows.append(r)
            self._chunk_rows[idx].update_status(status, downloaded, size)
        self.after(0, _u)

    def _cb_status(self, text):
        def _u():
            c = TEXT_SECONDARY
            if "✅" in text: c = GREEN
            elif "⚠" in text or "devam" in text.lower(): c = ACCENT
            elif "iptal" in text.lower(): c = ORANGE
            self.status_label.configure(text=text, text_color=c)
        self.after(0, _u)

    def _cb_complete(self, path):
        self.after(0, lambda: (self.main_progress.configure(progress_color=GREEN),
                               self.main_progress.set(1.0),
                               self.pct_label.configure(text="100%", text_color=GREEN),
                               self.eta_label.configure(text="Kalan: 0s", text_color=GREEN)))

    def _cb_error(self, err):
        self.after(0, lambda: (self.main_progress.configure(progress_color=RED),
                               self.status_label.configure(text=f"❌ Hata: {err}", text_color=RED)))

    def _set_ui_state(self, downloading):
        if downloading:
            self.dl_btn.configure(state="disabled"); self.pause_btn.configure(state="normal")
            self.cancel_btn.configure(state="normal"); self.url_entry.configure(state="disabled")
            self.conn_slider.configure(state="disabled")
        else:
            self.dl_btn.configure(state="normal"); self.pause_btn.configure(state="disabled", text="⏸  Duraklat")
            self.cancel_btn.configure(state="disabled"); self.url_entry.configure(state="normal")
            self.conn_slider.configure(state="normal")

    def destroy(self):
        if self._download_running and self.engine:
            self.engine.cancel()
            if self._thread and self._thread.is_alive(): self._thread.join(timeout=3)
        self.scheduler.stop_all()
        if self._tray_icon:
            try: self._tray_icon.stop()
            except Exception: pass
        super().destroy()
