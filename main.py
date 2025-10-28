# ===== file: main.py =====
from PyQt5.QtWidgets import QApplication
from ui.main_window import MainWindow
import sys


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()