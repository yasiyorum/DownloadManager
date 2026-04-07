"""
dialogs.py — Tüm dialog pencereleri
────────────────────────────────────
Ayarlar, Geçmiş, Toplu İndirme, Video İndirme,
Tarayıcı Kurulumu, Zamanlı İndirme
"""

import os
import threading
from tkinter import filedialog, messagebox

import customtkinter as ctk
from download_engine import DownloadEngine
from features import (
    SettingsManager, DownloadHistory, AutoStart, AutoExtractor,
    VideoDownloader, DownloadScheduler, Notifications,
    FileCategorizer,
)

# Renkler (gui.py ile uyumlu)
BG_DARK = "#0d1117"
BG_CARD = "#161b22"
BG_INPUT = "#1c2128"
BORDER = "#30363d"
ACCENT = "#1f6feb"
ACCENT_H = "#388bfd"
GREEN = "#2ea043"
RED = "#da3633"
ORANGE = "#d29922"
TXT = "#e6edf3"
TXT2 = "#8b949e"
TXT3 = "#484f58"
FONT = "Segoe UI"


def _dialog_base(parent, title, w, h):
    d = ctk.CTkToplevel(parent)
    d.title(title)
    d.geometry(f"{w}x{h}")
    d.configure(fg_color=BG_DARK)
    d.transient(parent)
    d.grab_set()
    d.update_idletasks()
    x = parent.winfo_x() + (parent.winfo_width() - w) // 2
    y = parent.winfo_y() + (parent.winfo_height() - h) // 2
    d.geometry(f"+{x}+{y}")
    return d


# ═══════════════════ Ayarlar Dialogu ═══════════════════
class SettingsDialog:
    def __init__(self, parent, settings: SettingsManager):
        self.settings = settings
        d = _dialog_base(parent, "⚙  Ayarlar", 520, 560)
        self.dialog = d

        scroll = ctk.CTkScrollableFrame(d, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=16, pady=16)

        # ── Hız Sınırlama ──
        self._section(scroll, "🚀 Hız Sınırlama")
        f = ctk.CTkFrame(scroll, fg_color=BG_CARD, corner_radius=10)
        f.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(f, text="Maksimum indirme hızı (0 = sınırsız)",
                     font=(FONT, 12), text_color=TXT2).pack(padx=14, pady=(10, 2), anchor="w")
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(0, 10))
        self.speed_entry = ctk.CTkEntry(row, width=100, fg_color=BG_INPUT,
                                         border_color=BORDER, text_color=TXT)
        self.speed_entry.pack(side="left")
        self.speed_entry.insert(0, str(settings.get("speed_limit", 0) // 1024))
        ctk.CTkLabel(row, text="KB/s", font=(FONT, 12), text_color=TXT2).pack(side="left", padx=8)

        # ── Varsayılan Bağlantı ──
        self._section(scroll, "🔗 Varsayılan Bağlantı Sayısı")
        f2 = ctk.CTkFrame(scroll, fg_color=BG_CARD, corner_radius=10)
        f2.pack(fill="x", pady=(0, 10))
        row2 = ctk.CTkFrame(f2, fg_color="transparent")
        row2.pack(fill="x", padx=14, pady=10)
        self.conn_slider = ctk.CTkSlider(row2, from_=1, to=64, number_of_steps=63,
                                          fg_color=BG_INPUT, progress_color=ACCENT)
        self.conn_slider.set(settings.get("default_connections", 16))
        self.conn_slider.pack(side="left", fill="x", expand=True)
        self.conn_lbl = ctk.CTkLabel(row2, text=str(settings.get("default_connections", 16)),
                                      font=(FONT, 13, "bold"), text_color=ACCENT, width=30)
        self.conn_lbl.pack(side="right", padx=8)
        self.conn_slider.configure(command=lambda v: self.conn_lbl.configure(text=str(int(v))))

        # ── Toggle'lar ──
        self._section(scroll, "📦 Otomatik İşlemler")
        toggles_frame = ctk.CTkFrame(scroll, fg_color=BG_CARD, corner_radius=10)
        toggles_frame.pack(fill="x", pady=(0, 10))

        self.auto_extract_var = ctk.BooleanVar(value=settings.get("auto_extract", False))
        self.auto_cat_var = ctk.BooleanVar(value=settings.get("auto_categorize", False))
        self.notify_var = ctk.BooleanVar(value=settings.get("notify_on_complete", True))
        self.autostart_var = ctk.BooleanVar(value=AutoStart.is_enabled())

        for text, var in [
            ("İndirme sonrası arşivleri otomatik aç", self.auto_extract_var),
            ("Dosyaları türüne göre klasörle", self.auto_cat_var),
            ("İndirme bitince bildirim göster", self.notify_var),
            ("Windows başlangıcında otomatik çalıştır", self.autostart_var),
        ]:
            ctk.CTkSwitch(toggles_frame, text=text, font=(FONT, 12),
                          variable=var, fg_color=BORDER,
                          progress_color=ACCENT).pack(padx=14, pady=6, anchor="w")

        # ── Kayıt Yolu ──
        self._section(scroll, "📁 Varsayılan Kayıt Yolu")
        f3 = ctk.CTkFrame(scroll, fg_color=BG_CARD, corner_radius=10)
        f3.pack(fill="x", pady=(0, 10))
        row3 = ctk.CTkFrame(f3, fg_color="transparent")
        row3.pack(fill="x", padx=14, pady=10)
        self.dir_entry = ctk.CTkEntry(row3, fg_color=BG_INPUT, border_color=BORDER, text_color=TXT)
        self.dir_entry.pack(side="left", fill="x", expand=True)
        self.dir_entry.insert(0, settings.get("save_dir", ""))
        ctk.CTkButton(row3, text="📁", width=36, fg_color=BG_INPUT,
                       hover_color=ACCENT, command=self._browse).pack(side="right", padx=(8, 0))

        # ── Butonlar ──
        btn_frame = ctk.CTkFrame(d, fg_color="transparent")
        btn_frame.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkButton(btn_frame, text="💾 Kaydet", fg_color=GREEN, hover_color="#3fb950",
                       font=(FONT, 13, "bold"), height=40,
                       command=self._save).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(btn_frame, text="İptal", fg_color=BORDER, hover_color=TXT3,
                       font=(FONT, 13), height=40,
                       command=d.destroy).pack(side="right", fill="x", expand=True, padx=(4, 0))

    def _section(self, parent, text):
        ctk.CTkLabel(parent, text=text, font=(FONT, 14, "bold"),
                     text_color=TXT).pack(padx=4, pady=(12, 4), anchor="w")

    def _browse(self):
        d = filedialog.askdirectory()
        if d:
            self.dir_entry.delete(0, "end")
            self.dir_entry.insert(0, d)

    def _save(self):
        try:
            speed = int(self.speed_entry.get()) * 1024
        except ValueError:
            speed = 0
        self.settings.set("speed_limit", max(speed, 0))
        self.settings.set("default_connections", int(self.conn_slider.get()))
        self.settings.set("auto_extract", self.auto_extract_var.get())
        self.settings.set("auto_categorize", self.auto_cat_var.get())
        self.settings.set("notify_on_complete", self.notify_var.get())
        self.settings.set("save_dir", self.dir_entry.get())

        if self.autostart_var.get():
            AutoStart.enable()
        else:
            AutoStart.disable()
        self.settings.set("auto_start", self.autostart_var.get())
        self.dialog.destroy()


# ═══════════════════ Geçmiş Dialogu ═══════════════════
class HistoryDialog:
    def __init__(self, parent, history: DownloadHistory):
        self.history = history
        d = _dialog_base(parent, "📊  İndirme Geçmişi", 700, 500)

        # Arama
        top = ctk.CTkFrame(d, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(16, 8))
        self.search_entry = ctk.CTkEntry(top, placeholder_text="Ara...",
                                          fg_color=BG_INPUT, border_color=BORDER, text_color=TXT)
        self.search_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(top, text="🔍", width=40, fg_color=ACCENT, hover_color=ACCENT_H,
                       command=self._search).pack(side="left", padx=(8, 0))
        ctk.CTkButton(top, text="🗑 Temizle", width=80, fg_color=RED, hover_color="#f85149",
                       command=self._clear).pack(side="right", padx=(8, 0))

        # Liste
        self.scroll = ctk.CTkScrollableFrame(d, fg_color=BG_DARK, corner_radius=8)
        self.scroll.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.scroll.grid_columnconfigure(0, weight=1)

        self._load_items(history.get_all())

    def _load_items(self, items):
        for w in self.scroll.winfo_children():
            w.destroy()
        if not items:
            ctk.CTkLabel(self.scroll, text="Geçmiş boş", font=(FONT, 12),
                         text_color=TXT3).pack(pady=20)
            return
        for i, item in enumerate(items):
            row = ctk.CTkFrame(self.scroll, fg_color=BG_CARD, corner_radius=8, height=40)
            row.grid(row=i, column=0, sticky="ew", pady=2)
            row.grid_columnconfigure(1, weight=1)
            st = "✅" if item["status"] == "completed" else "❌"
            ctk.CTkLabel(row, text=st, width=28).grid(row=0, column=0, padx=(8, 2))
            ctk.CTkLabel(row, text=item.get("filename", "?"), font=(FONT, 12),
                         text_color=TXT, anchor="w").grid(row=0, column=1, sticky="ew", padx=4)
            size = DownloadEngine._format_size(item.get("size", 0)) if item.get("size") else "-"
            ctk.CTkLabel(row, text=size, font=(FONT, 11), text_color=TXT3,
                         width=70).grid(row=0, column=2, padx=4)
            ctk.CTkLabel(row, text=item.get("date", ""), font=(FONT, 10),
                         text_color=TXT3, width=110).grid(row=0, column=3, padx=(4, 8))

    def _search(self):
        q = self.search_entry.get().strip()
        items = self.history.search(q) if q else self.history.get_all()
        self._load_items(items)

    def _clear(self):
        if messagebox.askyesno("Geçmişi Temizle", "Tüm indirme geçmişi silinecek. Emin misiniz?"):
            self.history.clear()
            self._load_items([])


# ═══════════════════ Toplu İndirme Dialogu ═══════════════════
class BatchDialog:
    def __init__(self, parent, on_add):
        self.on_add = on_add
        d = _dialog_base(parent, "📋  Toplu İndirme", 550, 420)
        self.dialog = d

        ctk.CTkLabel(d, text="Her satıra bir URL yazın:", font=(FONT, 13),
                     text_color=TXT2).pack(padx=16, pady=(16, 8), anchor="w")

        self.textbox = ctk.CTkTextbox(d, fg_color=BG_INPUT, text_color=TXT,
                                       border_color=BORDER, corner_radius=8,
                                       font=(FONT, 12))
        self.textbox.pack(fill="both", expand=True, padx=16)

        ctk.CTkLabel(d, text="Veya bir metin dosyası seçin:", font=(FONT, 11),
                     text_color=TXT3).pack(padx=16, pady=(8, 4), anchor="w")

        btn_row = ctk.CTkFrame(d, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 16))

        ctk.CTkButton(btn_row, text="📄 Dosyadan Yükle", width=140,
                       fg_color=BG_INPUT, border_color=BORDER, border_width=1,
                       hover_color=ACCENT, command=self._load_file).pack(side="left")
        ctk.CTkButton(btn_row, text="⬇ Tümünü Kuyruğa Ekle", fg_color=GREEN,
                       hover_color="#3fb950", font=(FONT, 13, "bold"), height=40,
                       command=self._add_all).pack(side="right")

    def _load_file(self):
        f = filedialog.askopenfilename(filetypes=[("Metin dosyası", "*.txt"), ("Tümü", "*.*")])
        if f:
            with open(f, "r", encoding="utf-8") as fh:
                self.textbox.delete("1.0", "end")
                self.textbox.insert("1.0", fh.read())

    def _add_all(self):
        text = self.textbox.get("1.0", "end").strip()
        urls = [u.strip() for u in text.splitlines() if u.strip() and u.strip().startswith("http")]
        if not urls:
            messagebox.showwarning("Uyarı", "Geçerli URL bulunamadı.")
            return
        for url in urls:
            self.on_add(url)
        messagebox.showinfo("Eklendi", f"{len(urls)} indirme kuyruğa eklendi.")
        self.dialog.destroy()


# ═══════════════════ Video İndirme Dialogu ═══════════════════
class VideoDialog:
    def __init__(self, parent, save_dir, on_add_queue=None):
        self.save_dir = save_dir
        self.on_add_queue = on_add_queue
        d = _dialog_base(parent, "🎬  Video İndirici", 520, 380)
        self.dialog = d

        if not VideoDownloader.is_available():
            ctk.CTkLabel(d, text="yt-dlp bulunamadı!", font=(FONT, 14, "bold"),
                         text_color=RED).pack(pady=(30, 10))
            ctk.CTkLabel(d, text="Video indirmek için yt-dlp gereklidir.",
                         font=(FONT, 12), text_color=TXT2).pack()
            ctk.CTkButton(d, text="📦 yt-dlp Kur (pip install)", height=40,
                           fg_color=ACCENT, hover_color=ACCENT_H,
                           command=self._install_ytdlp).pack(pady=20)
            return

        ctk.CTkLabel(d, text="Video URL:", font=(FONT, 13),
                     text_color=TXT2).pack(padx=16, pady=(16, 4), anchor="w")
        self.url_entry = ctk.CTkEntry(d, fg_color=BG_INPUT, border_color=BORDER,
                                       text_color=TXT, height=40, font=(FONT, 13))
        self.url_entry.pack(fill="x", padx=16)

        self.info_label = ctk.CTkLabel(d, text="", font=(FONT, 11), text_color=TXT2)
        self.info_label.pack(padx=16, pady=8)

        self.output_text = ctk.CTkTextbox(d, fg_color=BG_INPUT, text_color=TXT,
                                           height=120, font=(FONT, 10))
        self.output_text.pack(fill="x", padx=16)

        btn_row = ctk.CTkFrame(d, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=16)
        ctk.CTkButton(btn_row, text="ℹ Bilgi Al", fg_color=ACCENT, hover_color=ACCENT_H,
                       command=self._get_info).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_row, text="⬇ İndir", fg_color=GREEN, hover_color="#3fb950",
                       font=(FONT, 13, "bold"), height=40,
                       command=self._download).pack(side="right")

    def _install_ytdlp(self):
        self.dialog.destroy()
        threading.Thread(target=VideoDownloader.install, daemon=True).start()
        messagebox.showinfo("Kurulum", "yt-dlp kuruluyor... Lütfen bekleyin ve tekrar deneyin.")

    def _get_info(self):
        url = self.url_entry.get().strip()
        if not url:
            return
        self.info_label.configure(text="Bilgi alınıyor...")
        def _work():
            info = VideoDownloader.get_info(url)
            if info:
                title = info.get("title", "?")
                dur = info.get("duration", 0)
                self.dialog.after(0, lambda: self.info_label.configure(
                    text=f"📹 {title} ({dur // 60}:{dur % 60:02d})", text_color=GREEN))
            else:
                self.dialog.after(0, lambda: self.info_label.configure(
                    text="❌ Bilgi alınamadı", text_color=RED))
        threading.Thread(target=_work, daemon=True).start()

    def _download(self):
        url = self.url_entry.get().strip()
        if not url:
            return
        self.info_label.configure(text="İndiriliyor...", text_color=ACCENT)
        def _output(line):
            self.dialog.after(0, lambda l=line: self.output_text.insert("end", l + "\n"))
            self.dialog.after(0, lambda: self.output_text.see("end"))
        def _work():
            ok = VideoDownloader.download(url, self.save_dir, on_output=_output)
            self.dialog.after(0, lambda: self.info_label.configure(
                text="✅ Tamamlandı!" if ok else "❌ Hata!", text_color=GREEN if ok else RED))
        threading.Thread(target=_work, daemon=True).start()


# ═══════════════════ Zamanlı İndirme Dialogu ═══════════════════
class ScheduleDialog:
    def __init__(self, parent, scheduler: DownloadScheduler, on_add_queue=None):
        self.scheduler = scheduler
        self.on_add_queue = on_add_queue
        d = _dialog_base(parent, "⏰  Zamanlı İndirme", 480, 380)
        self.dialog = d

        # Yeni zamanlama
        ctk.CTkLabel(d, text="Yeni Zamanlı İndirme", font=(FONT, 14, "bold"),
                     text_color=TXT).pack(padx=16, pady=(16, 8), anchor="w")

        f = ctk.CTkFrame(d, fg_color=BG_CARD, corner_radius=10)
        f.pack(fill="x", padx=16, pady=(0, 8))

        ctk.CTkLabel(f, text="URL:", font=(FONT, 12), text_color=TXT2).pack(padx=14, pady=(10, 2), anchor="w")
        self.url_entry = ctk.CTkEntry(f, fg_color=BG_INPUT, border_color=BORDER, text_color=TXT)
        self.url_entry.pack(fill="x", padx=14)

        row = ctk.CTkFrame(f, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(8, 10))

        ctk.CTkLabel(row, text="Saat (SS:DD):", font=(FONT, 12), text_color=TXT2).pack(side="left")
        self.time_entry = ctk.CTkEntry(row, width=80, fg_color=BG_INPUT,
                                        border_color=BORDER, text_color=TXT,
                                        placeholder_text="23:00")
        self.time_entry.pack(side="left", padx=8)

        ctk.CTkButton(row, text="⏰ Zamanla", fg_color=GREEN, hover_color="#3fb950",
                       font=(FONT, 12, "bold"), height=32,
                       command=self._schedule).pack(side="right")

        # Mevcut zamanlamalar
        ctk.CTkLabel(d, text="Zamanlanmış İndirmeler", font=(FONT, 14, "bold"),
                     text_color=TXT).pack(padx=16, pady=(8, 4), anchor="w")

        self.list_frame = ctk.CTkScrollableFrame(d, fg_color=BG_DARK, corner_radius=8, height=100)
        self.list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.list_frame.grid_columnconfigure(0, weight=1)

        self._refresh()

    def _refresh(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        items = self.scheduler.get_all()
        if not items:
            ctk.CTkLabel(self.list_frame, text="Zamanlanmış indirme yok",
                         font=(FONT, 11), text_color=TXT3).pack(pady=10)
            return
        for i, item in enumerate(items):
            row = ctk.CTkFrame(self.list_frame, fg_color=BG_CARD, corner_radius=8)
            row.grid(row=i, column=0, sticky="ew", pady=2)
            row.grid_columnconfigure(0, weight=1)
            url_short = item["url"][:40] + "..." if len(item["url"]) > 40 else item["url"]
            ctk.CTkLabel(row, text=f"⏰ {item['time']} — {url_short}",
                         font=(FONT, 11), text_color=TXT, anchor="w").grid(
                row=0, column=0, padx=10, pady=6, sticky="ew")
            ctk.CTkButton(row, text="✖", width=28, fg_color="transparent",
                           hover_color=RED, text_color=TXT3,
                           command=lambda sid=item["id"]: self._cancel(sid)).grid(
                row=0, column=1, padx=(0, 8), pady=6)

    def _schedule(self):
        url = self.url_entry.get().strip()
        t = self.time_entry.get().strip()
        if not url or not t:
            return
        item = self.scheduler.schedule(url, t)
        if item:
            self.url_entry.delete(0, "end")
            self.time_entry.delete(0, "end")
            self._refresh()
            messagebox.showinfo("Zamanlandı", f"İndirme {item['time']} saatinde başlayacak.")
        else:
            messagebox.showerror("Hata", "Geçersiz saat formatı. SS:DD kullanın.")

    def _cancel(self, sid):
        self.scheduler.cancel(sid)
        self._refresh()
