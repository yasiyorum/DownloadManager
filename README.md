# ⚡ Agresif İndirme Yöneticisi v3.0

Python tabanlı, çoklu bağlantılı, saf ve güvenilir profesyonel indirme yöneticisi.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

## ✨ Özellikler

### ⬇ İndirme Motoru
- **Worker Pool Bağlantı Yuvaları** — IDM tarzı gerçek zamanlı `#01..#16` bağlantı yuvası görselleştirmesi (CPU/Bellek dostu)
- **Agresif Chunking** — 4 MB mikro-chunk'lar, 1-64 eşzamanlı bağlantı
- **Akıllı Range Tespiti & Fallback** — Cloudflare, Drive veya MediaFire gibi HEAD engelleyen sunuculara karşı GET (Range: 0-1) koruması ve tekil akış desteği
- **Kalıcı Devam (Persistent State)** — `.download_states` ile uygulama/bilgisayar kapansa bile byte seviyesinde kaldığı yerden devam
- **Hızlı Birleştirme (Instant Merge)** — Tek parçalı indirmelerde sıfır disk I/O ile anında dosya tamamlama
- **Otomatik Retry** — Exponential backoff ile 8 deneme
- **EMA Hız Gösterimi** — Stabil, dalgalanmasız hız bilgisi
- **🚀 Hız Sınırlama** — İstenilen hızda indirme (KB/s) limiti koyabilme
- **Güvenli Dosya Adlandırma** — Windows rezerve kelimeleri (`CON`, `PRN`, `AUX`, vb.) ve geçersiz karakterleri (`<>:"/\|?*`) otomatik temizleme

### 📋 Kuyruk & Zamanlama
- **Sıralı İndirme Kuyruğu** — Birden fazla indirmeyi otomatik olarak sıraya dizer, "▶ Başlat" ve "🗑 Temizle" kontrolleri
- **🔗 Toplu İndirme** — URL listesi veya `.txt` metin dosyasından toplu indirme (HTTP, HTTPS, Magnet, FTP)
- **⏰ Kalıcı Zamanlı İndirme** — Belirlenen saatte otomatik başlatma (uygulama kapansa bile `~/.agresif_dm/schedules.json` ile korunur)

### 📦 Post-İndirme
- **📦 Otomatik Arşiv Açma** — ZIP, TAR, GZ, BZ2 otomatik çıkarma (klasör korumalı, veri kaybını önler)
- **🗂️ Dosya Kategorilendirme** — Video, Müzik, Belge vb. türüne göre klasörlere güvenli otomatik ayırma
- **📊 İndirme Geçmişi** — SQLite tabanlı, doğrudan dosya açma ("▶") ve klasörde seçerek gösterme ("📁") butonlu arama yapılabilir geçmiş

### 🎬 Medya ve Torrent İndirici
- **🎬 Video İndirme (yt-dlp)** — YouTube ve diğer sitelerden video indirme; canlı hız, kalan süre ve gerçek dosya adı yakalama
- **🌐 Torrent Desteği** — `magnet:?` linklerini ve `.torrent` dosyalarını asenkron olay döngüsü, canlı eş (peer) ve durum monitörü ile eksiksiz indirme

### 🖥 Uygulama
- **System Tray** — Arka planda çalışma, tray ikonundan bildirim ve kontrol
- **🔔 Bildirimler** — Windows toast bildirimleri (indirme tamamlandığında sesli ve görsel uyarır)
- **🔄 Otomatik Başlangıç** — Windows startup (Registry) entegrasyonu
- **Modern GUI** — CustomTkinter ile koyu temalı, 60 FPS akıcı ve profesyonel arayüz

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
