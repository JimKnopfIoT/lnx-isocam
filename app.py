#!/usr/bin/env python3
"""Entry point for iSoCam — a Linux client for the CRY8024 acoustic camera."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from isocam.ui.main import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("iSoCam")
    app.setDesktopFileName("lnx-isocam")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
