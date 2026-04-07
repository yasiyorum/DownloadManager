# ⚡ Agresif İndirme Yöneticisi v3.0

Python tabanlı, çoklu bağlantılı, saf ve güvenilir profesyonel indirme yöneticisi.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

## ✨ Özellikler

### ⬇ İndirme Motoru
- **Agresif Chunking** — 2 MB mikro-chunk'lar, 1-64 eşzamanlı bağlantı
- **Worker Pool** — Boşalan worker yeni iş alır, bant genişliğini maksimum kullanır
- **Kalıcı Devam** — Uygulama/bilgisayar kapansa bile kaldığı yerden devam
- **Otomatik Retry** — Exponential backoff ile 8 deneme
- **EMA Hız Gösterimi** — Stabil, dalgalanmasız hız bilgisi
- **🚀 Hız Sınırlama** — İstenilen hızda indirme (KB/s) limiti koyabilme

### 📋 Kuyruk & Zamanlama
- **Sıralı İndirme Kuyruğu** — Birden fazla indirmeyi otomatik olarak sıraya dizer
- **🔗 Toplu İndirme** — URL listesi veya `.txt` metin dosyasından toplu indirme
- **⏰ Zamanlı İndirme** — Belirlenen saatte otomatik başlatma (SS:DD formatı)

### 📦 Post-İndirme
- **📦 Otomatik Arşiv Açma** — ZIP, TAR, GZ, BZ2 otomatik çıkarma
- **🗂️ Dosya Kategorilendirme** — Video, Müzik, Belge vb. türüne göre klasörlere otomatik ayırma
- **📊 İndirme Geçmişi** — SQLite tabanlı arama yapılabilir, silinebilir kalıcı geçmiş

### 🎬 Medya ve Torrent İndirici
- **🎬 Video İndirme (yt-dlp)** — YouTube ve diğer sitelerden video indirme (python kütüphanesi olarak doğrudan uygulamaya gömülüdür, exe işlemlerini sıkıntıya sokmaz).
- **🌐 Torrent Desteği** — `magnet:?` linklerini ve `.torrent` dosyalarını otomatik algılar ve dahili modül ile indirir.

### 🖥 Uygulama
- **System Tray** — Arka planda çalışma, tray ikonundan kontrol
- **🔔 Bildirimler** — Windows toast bildirimleri (indirme tamamlandığında uyarır)
- **🔄 Otomatik Başlangıç** — Windows startup entegrasyonu
- **Modern GUI** — CustomTkinter ile koyu temalı, sade ve profesyonel arayüz

## 🚀 Kurulum

```bash
git clone https://github.com/yasiyorum/DownloadManager.git
cd DownloadManager
python -m venv venv
.\venv\Scripts\activate         # Windows
pip install -r requirements.txt
python main.py                    # Başlat
```

## 🏗️ Mimari

```
├── main.py                    # Giriş
├── download_engine.py         # Asenkron HTTP indirme motoru
├── gui.py                     # GUI arayüzü, indirme kuyruğu ve tray modülü
├── features.py                # Kategoriler, arşiv çıkartıcı, bildirimler, torrent ve ytdlp motoru
├── dialogs.py                 # Ayarlar, geçmiş ve diğer tüm alt pencereler
└── requirements.txt           # Bağımlılıklar
```

## 📋 Bağımlılıklar

| Paket | Açıklama |
|---|---|
| `aiohttp` | Asenkron HTTP istemci arayüzü |
| `aiofiles` | Asenkron dosya okuma/yazma |
| `customtkinter` | Modern Tkinter GUI motoru |
| `Pillow` | Arayüz/tepsi ikon oluşturucusu |
| `pystray` | System tray (bildirim çubuğu simgesi) |
| `yt-dlp` | Natively embedded video indirme kütüphanesi |
| `torrentp` | Torrent & Magnet link yöneticisi |

## 📄 Lisans

MIT License
