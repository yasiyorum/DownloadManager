# ⚡ Aggressive Multi-Connection Download Manager

Python tabanlı, **CustomTkinter** GUI'li, **asyncio + aiohttp** motorlu agresif çoklu bağlantılı indirme yöneticisi.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

## ✨ Özellikler

- **Agresif Chunking**: HTTP Range başlıkları ile dosyayı 2 MB'lık mikro-chunk'lara böler
- **Worker Pool**: 1-64 arası eşzamanlı bağlantı — bant genişliğini maksimum kullanır
- **Stabil Hız**: EMA (Exponential Moving Average) ile yumuşatılmış hız gösterimi
- **Kalıcı Devam (Persistent Resume)**: Uygulama kapansa, bilgisayar kapansa bile kaldığı yerden devam eder
- **Otomatik Retry**: Başarısız chunk'lar için üstel geri çekilme (exponential backoff) ile 8 deneme
- **Modern GUI**: CustomTkinter ile koyu temalı, profesyonel arayüz
- **Kısmi Chunk Resume**: Yarım kalan chunk'ları kaldığı byte'tan sürdürür
- **Tahmini Kalan Süre**: ETA gösterimi ile ne kadar sürede biteceğini görün

## 📸 Ekran Görüntüsü

*Uygulama koyu temalı modern arayüz ile çalışır.*

## 🚀 Kurulum

```bash
# Repo'yu klonla
git clone https://github.com/yasiyorum/aggressive-download-manager.git
cd aggressive-download-manager

# Sanal ortam oluştur ve bağımlılıkları kur
python -m venv venv

# Windows
.\venv\Scripts\activate

# Linux/Mac
source venv/bin/activate

pip install -r requirements.txt
```

## ▶️ Kullanım

```bash
python main.py
```

1. URL kutusuna indirme bağlantısını yapıştırın
2. Bağlantı sayısı slider'ından agresiflik seviyesini ayarlayın (1-64)
3. **İNDİR** butonuna basın
4. İndirme sırasında **Duraklat** / **Devam** yapabilirsiniz
5. Uygulamayı kapatsanız bile bir sonraki açılışta kaldığı yerden devam eder

## 🏗️ Mimari

```
├── main.py              # Giriş noktası
├── download_engine.py   # Asenkron indirme motoru
├── gui.py               # CustomTkinter GUI
└── requirements.txt     # Bağımlılıklar
```

### İndirme Motoru

- **Worker Pool**: `asyncio.Queue` + N worker → boşalan worker yeni chunk alır
- **Mikro-Chunk**: 2 MB parçalar → yavaş chunk diğerlerini bloklamaz
- **Persistent State**: `.state.json` ile durum kalıcı olarak kaydedilir
- **Asenkron I/O**: `aiofiles` ile disk yazma event loop'u bloklamaz

## 📋 Bağımlılıklar

| Paket | Açıklama |
|---|---|
| `aiohttp` | Asenkron HTTP istemci |
| `aiofiles` | Asenkron dosya I/O |
| `customtkinter` | Modern Tkinter GUI |

## 📄 Lisans

MIT License
