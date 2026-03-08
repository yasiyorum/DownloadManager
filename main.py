"""
Agresif Çoklu Bağlantılı İndirme Yöneticisi
Ana giriş noktası — GUI'yi başlatır.
"""

from gui import DownloadManagerApp


def main():
    app = DownloadManagerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
