"""Tiny generic background worker so adb calls never freeze the Qt UI."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QThread, Signal


class Worker(QThread):
    """Run ``fn(*args)`` in a thread; emit ``done`` or ``failed``."""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable, *args) -> None:
        super().__init__()
        self._fn = fn
        self._args = args

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            self.done.emit(self._fn(*self._args))
        except Exception as exc:  # surfaced in the UI status bar
            self.failed.emit(str(exc))
