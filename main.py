#!/usr/bin/env python3
"""
Server Monitor — Punto de entrada principal.
Ejecutar: python main.py
"""
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from src.database import init_db
from src.ui.main_window import MainWindow
from src.ui.styles import DARK_THEME


def main() -> None:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("Server Monitor")
    app.setOrganizationName("DevOps Tools")
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_THEME)

    init_db()

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
