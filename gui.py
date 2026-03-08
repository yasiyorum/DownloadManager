"""
Download Manager — Modern CustomTkinter GUI v2
───────────────────────────────────────────────
• Kalıcı duraklatma/devam: uygulama kapansa bile kaldığı yerden devam
• Başlangıçta yarım kalan indirmeleri otomatik algılar
• EMA hız + kalan süre gösterimi
"""

import asyncio
import os
import threading
from tkinter import filedialog, messagebox

import customtkinter as ctk
from download_engine import DownloadEngine

# ──────────────── Tema Ayarları ────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Renkler
BG_DARK = "#0d1117"
BG_CARD = "#161b22"
BG_INPUT = "#1c2128"
BORDER_COLOR = "#30363d"
ACCENT = "#1f6feb"
ACCENT_HOVER = "#388bfd"
GREEN = "#2ea043"
RED = "#da3633"
ORANGE = "#d29922"
TEXT_PRIMARY = "#e6edf3"
TEXT_SECONDARY = "#8b949e"
TEXT_DIM = "#484f58"

FONT_FAMILY = "Segoe UI"


class ChunkRow(ctk.CTkFrame):
    """Chunk durum satırı widget'ı."""

    STATUS_COLORS = {
        "pending": TEXT_DIM,
        "downloading": ACCENT,
        "completed": GREEN,
        "failed": RED,
        "retrying": ORANGE,
    }

    STATUS_LABELS = {
        "pending": "Bekliyor",
        "downloading": "İndiriliyor",
        "completed": "Tamamlandı",
        "failed": "Başarısız",
        "retrying": "Yeniden...",
    }

    def __init__(self, master, idx: int, size: int, **kwargs):
        super().__init__(master, fg_color="transparent", height=28, **kwargs)
        self.grid_columnconfigure(1, weight=1)

        self.idx_label = ctk.CTkLabel(
            self, text=f"#{idx + 1:03d}", font=(FONT_FAMILY, 11),
            text_color=TEXT_SECONDARY, width=50, anchor="w"
        )
        self.idx_label.grid(row=0, column=0, padx=(8, 4), pady=1)

        self.progress = ctk.CTkProgressBar(
            self, height=8, corner_radius=4,
            fg_color=BG_INPUT, progress_color=ACCENT
        )
        self.progress.grid(row=0, column=1, sticky="ew", padx=4, pady=1)
        self.progress.set(0)

        self.status_label = ctk.CTkLabel(
            self, text="Bekliyor", font=(FONT_FAMILY, 11),
            text_color=TEXT_DIM, width=90, anchor="e"
        )
        self.status_label.grid(row=0, column=2, padx=(4, 8), pady=1)

        self._size = size

    def update_status(self, status: str, downloaded: int, size: int):
        color = self.STATUS_COLORS.get(status, TEXT_DIM)
        label = self.STATUS_LABELS.get(status, status)

        pct = downloaded / size if size > 0 else 0
        self.progress.set(min(pct, 1.0))

        if status == "completed":
            self.progress.configure(progress_color=GREEN)
        elif status == "failed":
            self.progress.configure(progress_color=RED)
        elif status == "retrying":
            self.progress.configure(progress_color=ORANGE)
        else:
            self.progress.configure(progress_color=ACCENT)

        self.status_label.configure(text=label, text_color=color)


class DownloadManagerApp(ctk.CTk):
    """Ana uygulama penceresi."""

    def __init__(self):
        super().__init__()

        self.title("⚡ Agresif İndirme Yöneticisi")
        self.geometry("780x720")
        self.minsize(680, 600)
        self.configure(fg_color=BG_DARK)

        self.engine = DownloadEngine()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._download_running = False
        self._chunk_rows: list[ChunkRow] = []
        self._pending_resume: dict | None = None

        self._save_dir = os.path.expanduser("~/Downloads")
        self._build_ui()

        # Başlangıçta yarım kalan indirmeleri kontrol et
        self.after(500, self._check_pending_downloads)

    # ═══════════════════ UI Oluşturma ═══════════════════
    def _build_ui(self):
        self.grid_rowconfigure(3, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # ── Başlık ──
        header = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=56)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        header.grid_propagate(False)

        ctk.CTkLabel(
            header, text="⚡  Agresif İndirme Yöneticisi",
            font=(FONT_FAMILY, 18, "bold"), text_color=TEXT_PRIMARY
        ).grid(row=0, column=0, padx=20, pady=14, sticky="w")

        ctk.CTkLabel(
            header, text="v2.0", font=(FONT_FAMILY, 12),
            text_color=TEXT_DIM
        ).grid(row=0, column=1, padx=20, pady=14, sticky="e")

        # ── URL Girişi ──
        url_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12)
        url_frame.grid(row=1, column=0, padx=16, pady=(12, 6), sticky="ew")
        url_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            url_frame, text="URL", font=(FONT_FAMILY, 13, "bold"),
            text_color=TEXT_SECONDARY
        ).grid(row=0, column=0, padx=(16, 8), pady=(14, 4), sticky="w")

        self.url_entry = ctk.CTkEntry(
            url_frame, placeholder_text="İndirme bağlantısını buraya yapıştırın...",
            font=(FONT_FAMILY, 13), height=40,
            fg_color=BG_INPUT, border_color=BORDER_COLOR, text_color=TEXT_PRIMARY
        )
        self.url_entry.grid(row=0, column=1, padx=(0, 8), pady=(14, 4), sticky="ew")

        self.paste_btn = ctk.CTkButton(
            url_frame, text="📋", width=42, height=40,
            font=(FONT_FAMILY, 16), fg_color=BG_INPUT,
            border_color=BORDER_COLOR, border_width=1,
            hover_color=ACCENT, command=self._paste_url
        )
        self.paste_btn.grid(row=0, column=2, padx=(0, 16), pady=(14, 4))

        # ── Ayar satırı ──
        ctk.CTkLabel(
            url_frame, text="Bağlantı", font=(FONT_FAMILY, 12),
            text_color=TEXT_SECONDARY
        ).grid(row=1, column=0, padx=(16, 8), pady=(4, 14), sticky="w")

        slider_box = ctk.CTkFrame(url_frame, fg_color="transparent")
        slider_box.grid(row=1, column=1, padx=(0, 8), pady=(4, 14), sticky="ew")
        slider_box.grid_columnconfigure(0, weight=1)

        self.conn_slider = ctk.CTkSlider(
            slider_box, from_=1, to=64, number_of_steps=63,
            fg_color=BG_INPUT, progress_color=ACCENT,
            button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            command=self._on_slider_change
        )
        self.conn_slider.set(16)
        self.conn_slider.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.conn_label = ctk.CTkLabel(
            slider_box, text="16", font=(FONT_FAMILY, 13, "bold"),
            text_color=ACCENT, width=36
        )
        self.conn_label.grid(row=0, column=1)

        self.folder_btn = ctk.CTkButton(
            url_frame, text="📁 Kayıt Yolu", width=120, height=32,
            font=(FONT_FAMILY, 12), fg_color=BG_INPUT,
            border_color=BORDER_COLOR, border_width=1,
            hover_color=ACCENT, command=self._choose_folder
        )
        self.folder_btn.grid(row=1, column=2, padx=(0, 16), pady=(4, 14))

        # ── Kontrol Butonları ──
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=2, column=0, padx=16, pady=6, sticky="ew")
        btn_frame.grid_columnconfigure((0, 1, 2), weight=1)

        self.dl_btn = ctk.CTkButton(
            btn_frame, text="⬇  İNDİR", height=44,
            font=(FONT_FAMILY, 14, "bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._start_download
        )
        self.dl_btn.grid(row=0, column=0, padx=(0, 4), sticky="ew")

        self.pause_btn = ctk.CTkButton(
            btn_frame, text="⏸  Duraklat", height=44,
            font=(FONT_FAMILY, 14, "bold"),
            fg_color=ORANGE, hover_color="#e3a826",
            state="disabled", command=self._toggle_pause
        )
        self.pause_btn.grid(row=0, column=1, padx=4, sticky="ew")

        self.cancel_btn = ctk.CTkButton(
            btn_frame, text="✖  İptal + Sil", height=44,
            font=(FONT_FAMILY, 14, "bold"),
            fg_color=RED, hover_color="#f85149",
            state="disabled", command=self._cancel_download
        )
        self.cancel_btn.grid(row=0, column=2, padx=(4, 0), sticky="ew")

        # ── İlerleme Bölgesi ──
        progress_card = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12)
        progress_card.grid(row=3, column=0, padx=16, pady=6, sticky="nsew")
        progress_card.grid_rowconfigure(3, weight=1)
        progress_card.grid_columnconfigure(0, weight=1)

        # Genel ilerleme üst satır
        prog_top = ctk.CTkFrame(progress_card, fg_color="transparent")
        prog_top.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        prog_top.grid_columnconfigure(0, weight=1)

        self.pct_label = ctk.CTkLabel(
            prog_top, text="0%", font=(FONT_FAMILY, 22, "bold"),
            text_color=TEXT_PRIMARY
        )
        self.pct_label.grid(row=0, column=0, sticky="w")

        eta_speed_frame = ctk.CTkFrame(prog_top, fg_color="transparent")
        eta_speed_frame.grid(row=0, column=1, sticky="e")

        self.eta_label = ctk.CTkLabel(
            eta_speed_frame, text="Kalan: --", font=(FONT_FAMILY, 13),
            text_color=ACCENT, width=140, anchor="e"
        )
        self.eta_label.grid(row=0, column=0, padx=(0, 12))

        self.speed_label = ctk.CTkLabel(
            eta_speed_frame, text="— MB/s", font=(FONT_FAMILY, 13),
            text_color=TEXT_SECONDARY
        )
        self.speed_label.grid(row=0, column=1)

        self.main_progress = ctk.CTkProgressBar(
            progress_card, height=14, corner_radius=7,
            fg_color=BG_INPUT, progress_color=ACCENT
        )
        self.main_progress.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 4))
        self.main_progress.set(0)

        # Durum + boyut
        info_row = ctk.CTkFrame(progress_card, fg_color="transparent")
        info_row.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
        info_row.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            info_row, text="Hazır", font=(FONT_FAMILY, 12),
            text_color=TEXT_SECONDARY, anchor="w"
        )
        self.status_label.grid(row=0, column=0, sticky="w")

        self.size_label = ctk.CTkLabel(
            info_row, text="", font=(FONT_FAMILY, 12),
            text_color=TEXT_SECONDARY, anchor="e"
        )
        self.size_label.grid(row=0, column=1, sticky="e")

        # Chunk listesi
        self.chunk_scroll = ctk.CTkScrollableFrame(
            progress_card, fg_color=BG_DARK, corner_radius=8,
            scrollbar_button_color=BORDER_COLOR,
            scrollbar_button_hover_color=TEXT_DIM,
        )
        self.chunk_scroll.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.chunk_scroll.grid_columnconfigure(0, weight=1)

        # ── Alt Bar ──
        footer = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=32)
        footer.grid(row=4, column=0, sticky="ew")
        footer.grid_propagate(False)
        footer.grid_columnconfigure(0, weight=1)

        self.footer_label = ctk.CTkLabel(
            footer, text=f"Kayıt: {self._save_dir}",
            font=(FONT_FAMILY, 11), text_color=TEXT_DIM
        )
        self.footer_label.grid(row=0, column=0, padx=16, pady=6, sticky="w")

    # ═══════════════════ Yarıda Kalan İndirmeler ═══════════════════
    def _check_pending_downloads(self):
        """Başlangıçta yarım kalan indirmeleri kontrol eder."""
        pending = DownloadEngine.find_pending_downloads(self._save_dir)
        if not pending:
            return

        # En son kalan indirmeyi al
        latest = max(pending, key=lambda p: p["data"].get("timestamp", 0))
        data = latest["data"]
        fname = data["filename"]
        done = latest["completed_chunks"]
        total = latest["total_chunks"]
        dl_bytes = latest["downloaded_bytes"]
        total_bytes = data["total_size"]
        pct = (dl_bytes / total_bytes * 100) if total_bytes > 0 else 0

        answer = messagebox.askyesno(
            "Yarım Kalan İndirme Bulundu",
            f"Dosya: {fname}\n"
            f"İlerleme: {pct:.1f}% ({DownloadEngine._format_size(dl_bytes)} / "
            f"{DownloadEngine._format_size(total_bytes)})\n"
            f"Parçalar: {done}/{total} tamamlanmış\n\n"
            f"Kaldığı yerden devam etmek ister misiniz?",
        )

        if answer:
            self._pending_resume = latest
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, data["url"])
            self.conn_slider.set(data["connections"])
            self.conn_label.configure(text=str(data["connections"]))
            self._start_download(resume=True)

    # ═══════════════════ Aksiyonlar ═══════════════════
    def _paste_url(self):
        try:
            text = self.clipboard_get()
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, text.strip())
        except Exception:
            pass

    def _on_slider_change(self, val):
        v = int(val)
        self.conn_label.configure(text=str(v))

    def _choose_folder(self):
        d = filedialog.askdirectory(initialdir=self._save_dir)
        if d:
            self._save_dir = d
            self.footer_label.configure(text=f"Kayıt: {self._save_dir}")

    # ──────────────── İndirme Başlat / Devam ────────────────
    def _start_download(self, resume: bool = False):
        url = self.url_entry.get().strip()
        if not url and not resume:
            self.status_label.configure(text="⚠ Lütfen bir URL girin.", text_color=ORANGE)
            return

        connections = int(self.conn_slider.get())
        self._download_running = True
        self._set_ui_state(downloading=True)

        # Chunk satırlarını temizle
        for w in self.chunk_scroll.winfo_children():
            w.destroy()
        self._chunk_rows = []
        self.main_progress.set(0)
        self.main_progress.configure(progress_color=ACCENT)
        self.pct_label.configure(text="0%", text_color=TEXT_PRIMARY)
        self.speed_label.configure(text="— MB/s")
        self.eta_label.configure(text="Kalan: --")
        self.size_label.configure(text="")

        if resume and self._pending_resume:
            self.status_label.configure(
                text="Kaldığı yerden devam ediliyor...", text_color=ACCENT
            )
        else:
            self.status_label.configure(
                text="Başlatılıyor...", text_color=TEXT_SECONDARY
            )

        # Engine oluştur
        self.engine = DownloadEngine()
        self.engine.on_progress = self._cb_progress
        self.engine.on_chunk_update = self._cb_chunk
        self.engine.on_status = self._cb_status
        self.engine.on_complete = self._cb_complete
        self.engine.on_error = self._cb_error

        resume_state = self._pending_resume if resume else None
        self._pending_resume = None

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(url, connections, self._save_dir, resume_state),
            daemon=True,
        )
        self._thread.start()

    def _run_loop(self, url, connections, save_dir, resume_state):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(
            self.engine.start(url, connections, save_dir, resume_state)
        )
        self._download_running = False
        self.after(0, lambda: self._set_ui_state(downloading=False))

    # ──────────────── Duraklat / Devam ────────────────
    def _toggle_pause(self):
        if self.engine.is_paused:
            self.engine.resume()
            self.pause_btn.configure(text="⏸  Duraklat")
            self.status_label.configure(text="Devam ediliyor...", text_color=TEXT_SECONDARY)
        else:
            self.engine.pause()
            self.pause_btn.configure(text="▶  Devam")
            self.status_label.configure(
                text="Duraklatıldı — Uygulamayı kapatıp sonra devam edebilirsiniz",
                text_color=ORANGE
            )

    # ──────────────── Durdur (Kalıcı Devam İçin) ────────────────
    def _stop_download(self):
        """İndirmeyi durdurur ama durum dosyasını korur — sonra devam edilebilir."""
        self.engine.cancel()
        self.status_label.configure(
            text="⏹ Durduruldu — sonraki açılışta devam edebilirsiniz",
            text_color=ORANGE
        )

    # ──────────────── İptal + Sil ────────────────
    def _cancel_download(self):
        """İndirmeyi iptal eder ve tüm geçici dosyaları + state'i siler."""
        answer = messagebox.askyesno(
            "İndirmeyi İptal Et",
            "İndirme iptal edilecek ve tüm geçici dosyalar silinecek.\n"
            "Devam edilemez. Emin misiniz?",
        )
        if not answer:
            return

        self.engine.cancel()

        # State ve temp dosyalarını sil
        if hasattr(self.engine, '_state') and self.engine._state:
            if hasattr(self.engine, '_state_meta'):
                temp_dir = self.engine._state_meta.get("temp_dir", "")
                if temp_dir and os.path.isdir(temp_dir):
                    import shutil
                    try:
                        shutil.rmtree(temp_dir)
                    except OSError:
                        pass
            self.engine._state.delete()

        self.status_label.configure(text="❌ İndirme iptal edildi.", text_color=RED)

    # ═══════════════════ Callback'ler (thread-safe) ═══════════════════
    def _cb_progress(self, downloaded, total, speed):
        def _update():
            if total > 0:
                pct = downloaded / total
                self.main_progress.set(min(pct, 1.0))
                self.pct_label.configure(text=f"{pct * 100:.1f}%")
                self.size_label.configure(
                    text=f"{DownloadEngine._format_size(downloaded)} / "
                         f"{DownloadEngine._format_size(total)}"
                )
                # Tahmini kalan süre
                remaining_bytes = total - downloaded
                if speed > 0 and remaining_bytes > 0:
                    eta_seconds = remaining_bytes / speed
                    self.eta_label.configure(
                        text=f"Kalan: {DownloadEngine._format_time(eta_seconds)}",
                        text_color=ACCENT,
                    )
                elif remaining_bytes <= 0:
                    self.eta_label.configure(text="Kalan: 0s", text_color=GREEN)
                else:
                    self.eta_label.configure(
                        text="Kalan: hesaplanıyor...", text_color=TEXT_DIM
                    )
            self.speed_label.configure(text=DownloadEngine._format_speed(speed))
        self.after(0, _update)

    def _cb_chunk(self, idx, status, downloaded, size):
        def _update():
            while len(self._chunk_rows) <= idx:
                row = ChunkRow(self.chunk_scroll, len(self._chunk_rows), size)
                row.grid(row=len(self._chunk_rows), column=0, sticky="ew", pady=1)
                self._chunk_rows.append(row)
            self._chunk_rows[idx].update_status(status, downloaded, size)
        self.after(0, _update)

    def _cb_status(self, text):
        def _update():
            color = TEXT_SECONDARY
            if "✅" in text:
                color = GREEN
            elif "⚠" in text or "devam" in text.lower():
                color = ACCENT
            elif "iptal" in text.lower() or "durdur" in text.lower():
                color = ORANGE
            self.status_label.configure(text=text, text_color=color)
        self.after(0, _update)

    def _cb_complete(self, path):
        def _update():
            self.main_progress.configure(progress_color=GREEN)
            self.main_progress.set(1.0)
            self.pct_label.configure(text="100%", text_color=GREEN)
            self.eta_label.configure(text="Kalan: 0s", text_color=GREEN)
        self.after(0, _update)

    def _cb_error(self, err):
        def _update():
            self.main_progress.configure(progress_color=RED)
            self.status_label.configure(text=f"❌ Hata: {err}", text_color=RED)
        self.after(0, _update)

    # ═══════════════════ UI Durum Yönetimi ═══════════════════
    def _set_ui_state(self, downloading: bool):
        if downloading:
            self.dl_btn.configure(state="disabled")
            self.pause_btn.configure(state="normal")
            self.cancel_btn.configure(state="normal")
            self.url_entry.configure(state="disabled")
            self.conn_slider.configure(state="disabled")
        else:
            self.dl_btn.configure(state="normal")
            self.pause_btn.configure(state="disabled", text="⏸  Duraklat")
            self.cancel_btn.configure(state="disabled")
            self.url_entry.configure(state="normal")
            self.conn_slider.configure(state="normal")

    def destroy(self):
        """Pencere kapatılırken durumu kaydet."""
        if self._download_running and self.engine:
            self.engine.cancel()
            # Thread'in state yazmasını bekle
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=3)
        super().destroy()
