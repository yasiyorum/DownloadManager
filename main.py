"""
Agresif Çoklu Bağlantılı İndirme Yöneticisi v3
Ana giriş noktası — GUI'yi başlatır.
"""

import multiprocessing
from gui import DownloadManagerApp


def main():
    app = DownloadManagerApp()
    app.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
