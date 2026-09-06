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
    TorrentDownloader, ClipboardWatcher, VideoDownloader,
    BrowserDownloadWatcher,
)
from dialogs import (
    SettingsDialog, HistoryDialog, BatchDialog,
    ScheduleDialog,
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


class WorkerRow(ctk.CTkFrame):
    STATUS_COLORS = {
        "idle": TEXT_DIM,
        "downloading": ACCENT,
        "completed": GREEN,
        "failed": RED,
        "retrying": ORANGE,
    }
    STATUS_LABELS = {
        "idle": "Boşta",
        "downloading": "İndiriliyor",
        "completed": "Tamamlandı",
        "failed": "Başarısız",
        "retrying": "Yeniden...",
    }

    def __init__(self, master, worker_id, **kw):
        super().__init__(master, fg_color="transparent", height=28, **kw)
        self.grid_columnconfigure(1, weight=1)
        self.worker_id = worker_id
        self.idx_label = ctk.CTkLabel(
            self, text=f"Bağlantı #{worker_id+1:02d}",
            font=(FONT_FAMILY, 11, "bold"), text_color=TEXT_SECONDARY,
            width=85, anchor="w"
        )
        self.idx_label.grid(row=0, column=0, padx=(8, 4), pady=1)

        self.progress = ctk.CTkProgressBar(
            self, height=8, corner_radius=4, fg_color=BG_INPUT, progress_color=ACCENT
        )
        self.progress.grid(row=0, column=1, sticky="ew", padx=4, pady=1)
        self.progress.set(0)

        self.status_label = ctk.CTkLabel(
            self, text="Boşta", font=(FONT_FAMILY, 10),
            text_color=TEXT_DIM, width=105, anchor="e"
        )
        self.status_label.grid(row=0, column=2, padx=(4, 8), pady=1)

    def update_worker(self, chunk_idx, downloaded, size, status, speed=0.0):
        color = self.STATUS_COLORS.get(status, TEXT_DIM)
        pct = downloaded / size if size > 0 else 0
        self.progress.set(min(pct, 1.0))
        self.progress.configure(progress_color=color)

        if status == "downloading":
            ch_str = f"P#{chunk_idx+1}" if chunk_idx is not None else ""
            txt = f"{ch_str} (%{pct*100:.0f})" if ch_str else f"%{pct*100:.0f}"
        else:
            txt = self.STATUS_LABELS.get(status, status)

        self.status_label.configure(text=txt, text_color=color)


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
        self._download_running = False; self._worker_rows = []
        self._total_chunks = 0; self._completed_chunks = 0
        self._pending_resume = None; self._current_queue_item = None
        self._save_dir = self.settings.get("save_dir")
        self._download_queue = []
        self._tray_icon = None
        self._clipboard = ClipboardWatcher()
        self._clipboard.on_url_detected = self._on_clipboard_url
        self._clipboard_popup = None  # Active popup reference
        self._browser_watcher = BrowserDownloadWatcher()
        self._browser_watcher.on_download_detected = self._on_browser_download
        self._browser_popup = None

        self._build_ui()
        if HAS_TRAY: self._setup_tray()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # Pending download check in background to avoid startup lag
        self.after(300, lambda: threading.Thread(target=self._check_pending_downloads_bg, daemon=True).start())
        # Start clipboard monitoring
        self.after(1000, lambda: self._clipboard.start(self))
        # Start browser download watching
        self.after(2000, lambda: self._browser_watcher.start(self))

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
            ("⏰ Zamanlı", self._show_schedule),
            ("⚙ Ayarlar", self._show_settings),
        ]
        for i, (txt, cmd) in enumerate(tbtns):
            ctk.CTkButton(toolbar, text=txt, width=110, height=32, font=(FONT_FAMILY, 11),
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
        qh.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(qh, text="📋 İndirme Kuyruğu", font=(FONT_FAMILY, 13, "bold"),
                     text_color=TEXT_PRIMARY).grid(row=0, column=0, sticky="w")

        q_acts = ctk.CTkFrame(qh, fg_color="transparent")
        q_acts.grid(row=0, column=1, sticky="e")
        self.queue_count_label = ctk.CTkLabel(q_acts, text="Boş", font=(FONT_FAMILY, 11), text_color=TEXT_DIM)
        self.queue_count_label.pack(side="left", padx=(0, 8))
        ctk.CTkButton(q_acts, text="▶ Başlat", width=60, height=24, font=(FONT_FAMILY, 10, "bold"),
                      fg_color=GREEN, hover_color="#3fb950", command=self._process_queue).pack(side="left", padx=2)
        ctk.CTkButton(q_acts, text="🗑 Temizle", width=60, height=24, font=(FONT_FAMILY, 10),
                      fg_color=BG_INPUT, hover_color=RED, command=self._clear_queue).pack(side="left", padx=2)

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
        SettingsDialog(self, self.settings, on_save=self._apply_settings)
        self._apply_settings()

    def _show_history(self):
        HistoryDialog(self, self.history)

    def _show_batch(self):
        BatchDialog(self, self._add_url_to_queue)

    def _show_schedule(self):
        ScheduleDialog(self, self.scheduler)

    # ═══════════════ Clipboard Link Yakalama ═══════════════
    def _on_clipboard_url(self, url, is_video):
        """Clipboard'da indirilebilir URL algılandığında çağrılır."""
        if self._download_running:
            return
        if self.state() == "iconic" or not self.winfo_viewable():
            type_txt = "Video linki" if is_video else "İndirme linki"
            Notifications.show("⚡ Link Algılandı", f"{type_txt}: {url[:45]}...")
        if self._clipboard_popup and self._clipboard_popup.winfo_exists():
            return
        self._show_clipboard_popup(url, is_video)

    def _show_clipboard_popup(self, url, is_video):
        """Clipboard'dan yakalanan URL için floating popup göster."""
        # Eski popup varsa kapat
        if self._clipboard_popup and self._clipboard_popup.winfo_exists():
            self._clipboard_popup.destroy()

        popup = ctk.CTkFrame(self, fg_color="#1c2128", corner_radius=12,
                              border_width=1, border_color=ACCENT)
        popup.place(relx=0.5, rely=0.0, anchor="n", y=62)
        popup.lift()
        self._clipboard_popup = popup

        # İçerik
        inner = ctk.CTkFrame(popup, fg_color="transparent")
        inner.pack(padx=12, pady=10)

        icon = "🎬" if is_video else "🔗"
        type_text = "Video linki" if is_video else "İndirme linki"

        ctk.CTkLabel(inner, text=f"{icon} {type_text} algılandı!",
                     font=(FONT_FAMILY, 13, "bold"), text_color=TEXT_PRIMARY
                     ).pack(anchor="w")

        # URL göster (kısa)
        short_url = url[:60] + "..." if len(url) > 60 else url
        ctk.CTkLabel(inner, text=short_url, font=(FONT_FAMILY, 10),
                     text_color=TEXT_DIM).pack(anchor="w", pady=(2, 6))

        btn_row = ctk.CTkFrame(inner, fg_color="transparent")
        btn_row.pack(fill="x")

        ctk.CTkButton(btn_row, text="⬇ İndir", width=90, height=30,
                       font=(FONT_FAMILY, 11, "bold"), fg_color=ACCENT,
                       hover_color=ACCENT_HOVER,
                       command=lambda: self._accept_clipboard(url, popup)
                       ).pack(side="left", padx=(0, 4))

        ctk.CTkButton(btn_row, text="📋 Kuyruğa", width=90, height=30,
                       font=(FONT_FAMILY, 11), fg_color=GREEN,
                       hover_color="#3fb950",
                       command=lambda: self._queue_clipboard(url, popup)
                       ).pack(side="left", padx=4)

        ctk.CTkButton(btn_row, text="✖", width=30, height=30,
                       font=(FONT_FAMILY, 12), fg_color="transparent",
                       hover_color=RED, text_color=TEXT_DIM,
                       command=lambda: popup.destroy()
                       ).pack(side="right")

        # 8 saniye sonra otomatik kapat
        self.after(8000, lambda: popup.destroy() if popup.winfo_exists() else None)

    def _accept_clipboard(self, url, popup):
        """Clipboard URL'sini hemen indir."""
        popup.destroy()
        self.url_entry.configure(state="normal")
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, url)
        self._start_download()

    def _queue_clipboard(self, url, popup):
        """Clipboard URL'sini kuyruğa ekle."""
        popup.destroy()
        self._add_url_to_queue(url)

    # ═══════════════ Tarayıcı İndirme Yakalama ═══════════════
    def _on_browser_download(self, url, filename, crdownload_path):
        """Tarayıcıda yeni indirme algılandığında çağrılır."""
        if self._download_running:
            return
        if self.state() == "iconic" or not self.winfo_viewable():
            Notifications.show("⚡ Tarayıcı İndirmesi", f"{filename} algılandı!")
            self._tray_show()
        if self._browser_popup and self._browser_popup.winfo_exists():
            return
        # Clipboard popup varsa onu kapat
        if self._clipboard_popup and self._clipboard_popup.winfo_exists():
            self._clipboard_popup.destroy()
        self._show_browser_popup(url, filename, crdownload_path)

    def _show_browser_popup(self, url, filename, crdownload_path):
        """Tarayıcıdan yakalanan indirme için popup göster."""
        if self._browser_popup and self._browser_popup.winfo_exists():
            self._browser_popup.destroy()

        browser = self._browser_watcher.browser_name

        popup = ctk.CTkFrame(self, fg_color="#1c2128", corner_radius=12,
                              border_width=2, border_color=GREEN)
        popup.place(relx=0.5, rely=0.0, anchor="n", y=62)
        popup.lift()
        self._browser_popup = popup

        inner = ctk.CTkFrame(popup, fg_color="transparent")
        inner.pack(padx=14, pady=12)

        ctk.CTkLabel(inner, text=f"\U0001F310 {browser}'da indirme alg\u0131land\u0131!",
                     font=(FONT_FAMILY, 14, "bold"), text_color=GREEN
                     ).pack(anchor="w")

        # Dosya adı
        display_name = filename if len(filename) <= 50 else filename[:47] + "..."
        ctk.CTkLabel(inner, text=f"\U0001F4C4 {display_name}",
                     font=(FONT_FAMILY, 12), text_color=TEXT_PRIMARY
                     ).pack(anchor="w", pady=(4, 0))

        # URL (kısa)
        short_url = url[:55] + "..." if len(url) > 55 else url
        ctk.CTkLabel(inner, text=short_url, font=(FONT_FAMILY, 9),
                     text_color=TEXT_DIM).pack(anchor="w", pady=(2, 8))

        btn_row = ctk.CTkFrame(inner, fg_color="transparent")
        btn_row.pack(fill="x")

        ctk.CTkButton(btn_row, text="\u26a1 Devral ve \u0130ndir", width=140, height=32,
                       font=(FONT_FAMILY, 12, "bold"), fg_color=GREEN,
                       hover_color="#3fb950",
                       command=lambda: self._accept_browser_download(url, filename, crdownload_path, popup)
                       ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(btn_row, text="\U0001F4CB Kuyru\u011fa", width=90, height=32,
                       font=(FONT_FAMILY, 11), fg_color=ACCENT,
                       hover_color=ACCENT_HOVER,
                       command=lambda: self._queue_browser_download(url, crdownload_path, popup)
                       ).pack(side="left", padx=4)

        ctk.CTkButton(btn_row, text="\u2716", width=32, height=32,
                       font=(FONT_FAMILY, 13), fg_color="transparent",
                       hover_color=RED, text_color=TEXT_DIM,
                       command=lambda: popup.destroy()
                       ).pack(side="right")

        # 12 saniye sonra otomatik kapat
        self.after(12000, lambda: popup.destroy() if popup.winfo_exists() else None)

    def _accept_browser_download(self, url, filename, crdownload_path, popup):
        """Tarayıcı indirmesini devral: .crdownload sil, kendi motorumuzla indir."""
        popup.destroy()
        # Chrome'un indirmesini iptal et (.crdownload dosyasını sil)
        self._cancel_browser_download(crdownload_path)
        # Clipboard'a da işaretle
        self._clipboard.mark_url_seen(url)
        self._browser_watcher.mark_url_seen(url)
        # URL'yi gir ve indir
        self.url_entry.configure(state="normal")
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, url)
        self._start_download()

    def _queue_browser_download(self, url, crdownload_path, popup):
        """Tarayıcı indirmesini kuyruğa ekle."""
        popup.destroy()
        self._cancel_browser_download(crdownload_path)
        self._clipboard.mark_url_seen(url)
        self._browser_watcher.mark_url_seen(url)
        self._add_url_to_queue(url)

    def _cancel_browser_download(self, crdownload_path):
        """Chrome/Edge'in .crdownload dosyasını silerek indirmesini iptal et."""
        try:
            if crdownload_path and os.path.exists(crdownload_path):
                # Biraz bekle — Chrome dosyayı henüz kilitli tutabilir
                import time
                for _ in range(5):
                    try:
                        os.remove(crdownload_path)
                        return
                    except PermissionError:
                        time.sleep(0.3)
                # Son deneme
                os.remove(crdownload_path)
        except OSError:
            pass

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

    def _clear_queue(self):
        self._download_queue = [q for q in self._download_queue if q["status"] == "downloading"]
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
        self._clipboard.stop()
        self._browser_watcher.stop()
        if self._download_running and self.engine:
            self.engine.cancel()
            if self._thread and self._thread.is_alive(): self._thread.join(timeout=3)
        self.scheduler.stop_all()
        self.destroy()

    # ═══════════════ Pending Downloads ═══════════════
    def _check_pending_downloads_bg(self):
        """Background thread: scan for pending downloads, then prompt on main thread."""
        try:
            pending = DownloadEngine.find_pending_downloads(self._save_dir)
            if pending:
                latest = max(pending, key=lambda p: p["data"].get("timestamp", 0))
                self.after(0, lambda: self._prompt_resume(latest))
        except Exception:
            pass

    def _prompt_resume(self, latest):
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
        self._worker_rows = []
        self._total_chunks = 0
        self._completed_chunks = 0
        self.main_progress.set(0)
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
        self.engine.on_worker_update = self._cb_worker
        self.engine.on_status = self._cb_status
        self.engine.on_complete = self._cb_complete
        self.engine.on_error = self._cb_error

        # Clipboard'daki URL'yi görüldü olarak işaretle
        self._clipboard.mark_url_seen(url)

        # Torrent kontrol
        if TorrentDownloader.is_magnet_or_torrent(url):
            self._thread = threading.Thread(target=self._run_torrent, args=(url, self._save_dir), daemon=True)
            self._thread.start()
            return

        # Video URL kontrol — otomatik algılama
        if VideoDownloader.is_video_url(url):
            if not VideoDownloader.is_available():
                self.status_label.configure(text="⚠ Video indirmek için yt-dlp gerekli (pip install yt-dlp)", text_color=ORANGE)
                self._download_running = False
                self._set_ui_state(downloading=False)
                return
            self.status_label.configure(text="🎬 Video algılandı, yt-dlp ile indiriliyor...", text_color=ACCENT)
            self._thread = threading.Thread(target=self._run_video_download, args=(url, self._save_dir), daemon=True)
            self._thread.start()
            return

        # Çoklu bağlantı HTTP: Worker slotlarını oluştur
        for i in range(connections):
            r = WorkerRow(self.chunk_scroll, i)
            r.grid(row=i, column=0, sticky="ew", pady=1)
            self._worker_rows.append(r)

        resume_state = self._pending_resume if resume else None
        self._pending_resume = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop,
                                         args=(url, connections, self._save_dir, resume_state), daemon=True)
        self._thread.start()

    def _run_torrent(self, url, save_dir):
        def _on_prog(name, done, total, rate, peers, state):
            def _u():
                if total > 0:
                    p = done / total
                    self.main_progress.set(min(p, 1.0))
                    self.pct_label.configure(text=f"{p*100:.1f}%")
                    self.size_label.configure(text=f"{DownloadEngine._format_size(done)} / {DownloadEngine._format_size(total)}")
                else:
                    self.size_label.configure(text=DownloadEngine._format_size(done))
                if rate > 0:
                    self.speed_label.configure(text=DownloadEngine._format_speed(rate))
                    if total > done:
                        rem = (total - done) / rate
                        self.eta_label.configure(text=f"Kalan: {DownloadEngine._format_time(rem)}", text_color=ACCENT)
                self.status_label.configure(
                    text=f"🌐 Torrent: {state} — {peers} peer ({name[:25]})"[:60],
                    text_color=ACCENT
                )
            self.after(0, _u)

        ok, out_path, name = TorrentDownloader.download(
            url, save_dir, on_progress=_on_prog,
            cancel_check=lambda: not self._download_running
        )
        self._download_running = False
        if ok:
            self.engine._completed = True
            real_size = os.path.getsize(out_path) if (out_path and os.path.isfile(out_path)) else 0
            self.engine._state_meta = {
                "output_path": out_path or save_dir,
                "filename": name or "Torrent Download",
                "total_size": real_size,
                "url": url,
            }
            self.after(0, lambda: (
                self.main_progress.set(1.0),
                self.main_progress.configure(progress_color=GREEN),
                self.pct_label.configure(text="100%", text_color=GREEN),
                self.status_label.configure(text="✅ Torrent tamamlandı!", text_color=GREEN),
            ))
        else:
            self.after(0, lambda: (
                self.main_progress.configure(progress_color=RED),
                self.status_label.configure(text="❌ Torrent başarısız veya iptal edildi!", text_color=RED),
            ))
        self.after(0, self._on_download_finished)

    def _run_loop(self, url, connections, save_dir, resume_state):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self.engine.start(url, connections, save_dir, resume_state))
        except Exception:
            pass
        finally:
            self._download_running = False
            self.after(0, self._on_download_finished)

    def _run_video_download(self, url, save_dir):
        """yt-dlp ile video indirme — ana UI'daki progress'e yapılandırılmış entegrasyon."""
        def _on_prog(dl, tot, spd, eta, fn):
            def _u():
                if tot > 0:
                    p = dl / tot
                    self.main_progress.set(min(p, 1.0))
                    self.pct_label.configure(text=f"{p*100:.1f}%")
                    self.size_label.configure(text=f"{DownloadEngine._format_size(dl)} / {DownloadEngine._format_size(tot)}")
                if spd > 0:
                    self.speed_label.configure(text=DownloadEngine._format_speed(spd))
                if eta > 0:
                    self.eta_label.configure(text=f"Kalan: {DownloadEngine._format_time(eta)}", text_color=ACCENT)
                elif tot > 0 and dl >= tot:
                    self.eta_label.configure(text="Kalan: 0s", text_color=GREEN)
                if fn:
                    self.status_label.configure(text=f"🎬 {os.path.basename(fn)}"[:50], text_color=ACCENT)
            self.after(0, _u)

        def _on_out(msg):
            self.after(0, lambda m=msg: self.status_label.configure(text=m[:60], text_color=TEXT_SECONDARY))

        ok, out_path, title = VideoDownloader.download(
            url, save_dir, on_progress=_on_prog, on_output=_on_out,
            cancel_check=lambda: not self._download_running
        )
        self._download_running = False
        if ok:
            self.engine._completed = True
            real_size = os.path.getsize(out_path) if (out_path and os.path.isfile(out_path)) else 0
            self.engine._state_meta = {
                "output_path": out_path or save_dir,
                "filename": os.path.basename(out_path) if out_path else (title or "video.mp4"),
                "total_size": real_size,
                "url": url,
            }
            self.after(0, lambda: (
                self.main_progress.set(1.0),
                self.main_progress.configure(progress_color=GREEN),
                self.pct_label.configure(text="100%", text_color=GREEN),
                self.status_label.configure(text="✅ Video indirildi!", text_color=GREEN),
                self.eta_label.configure(text="Kalan: 0s", text_color=GREEN),
            ))
        else:
            self.after(0, lambda: (
                self.main_progress.configure(progress_color=RED),
                self.status_label.configure(text="❌ Video indirme başarısız veya iptal edildi!", text_color=RED),
            ))
        self.after(0, self._on_download_finished)

    def _on_download_finished(self):
        self._download_running = False
        self._set_ui_state(downloading=False)
        completed = getattr(self.engine, '_completed', False)

        if completed:
            # Post-download işlemleri
            meta = getattr(self.engine, '_state_meta', None)
            if meta:
                out = meta.get("output_path", "")
                fn = meta.get("filename", "")
                sz = meta.get("total_size", 0)
                url = meta.get("url", "")

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
        if not messagebox.askyesno("İptal", "İndirme iptal edilecek ve dosyalar silinecek. Emin misiniz?"): return
        # Stop the engine
        self.engine.cancel()
        self._download_running = False
        
        # Clean up temp files and state
        meta = getattr(self.engine, '_state_meta', None)
        if meta:
            td = meta.get("temp_dir", "")
            if td and os.path.isdir(td):
                import shutil
                try: shutil.rmtree(td)
                except OSError: pass
            # Also delete the output file if partially written
            op = meta.get("output_path", "")
            if op and os.path.exists(op):
                try: os.remove(op)
                except OSError: pass
        
        # Delete state file
        state = getattr(self.engine, '_state', None)
        if state:
            state.delete()
            # Boş kaldıysa .download_states klasörünü de sil
            state_dir = os.path.dirname(state.path)
            if os.path.isdir(state_dir) and not os.listdir(state_dir):
                try: os.rmdir(state_dir)
                except OSError: pass
        
        # Reset UI
        self._set_ui_state(downloading=False)
        self.status_label.configure(text="❌ İptal edildi ve dosyalar silindi.", text_color=RED)
        self.main_progress.set(0)
        self.main_progress.configure(progress_color=RED)
        self.pct_label.configure(text="0%", text_color=RED)
        self.speed_label.configure(text="— MB/s")
        self.eta_label.configure(text="Kalan: --")
        
        if self._current_queue_item:
            self._current_queue_item["status"] = "failed"
            self._current_queue_item = None; self._refresh_queue_ui()
        self.after(1000, self._process_queue)

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

    def _cb_worker(self, worker_id, chunk_idx, downloaded, size, status, speed=0.0):
        def _u():
            if worker_id < len(self._worker_rows):
                self._worker_rows[worker_id].update_worker(chunk_idx, downloaded, size, status, speed)
        self.after(0, _u)

    def _cb_chunk(self, idx, status, downloaded, size):
        def _u():
            if idx + 1 > self._total_chunks:
                self._total_chunks = idx + 1
            if status == "completed":
                self._completed_chunks += 1
            if self._total_chunks > 0:
                self.size_label.configure(
                    text=f"Parçalar: {min(self._completed_chunks, self._total_chunks)} / {self._total_chunks}"
                )
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
        self._clipboard.stop()
        self._browser_watcher.stop()
        if self._download_running and self.engine:
            self.engine.cancel()
            if self._thread and self._thread.is_alive(): self._thread.join(timeout=3)
        self.scheduler.stop_all()
        if self._tray_icon:
            try: self._tray_icon.stop()
            except Exception: pass
        super().destroy()
