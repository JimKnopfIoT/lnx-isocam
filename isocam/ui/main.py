"""Main window: Live tab (QtMultimedia) + Files tab (adb)."""

from __future__ import annotations

import os

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import device, formats
from ..live import HttpLiveWorker
from ..render import analysis_figure
from ..worker import Worker


_WATCH_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "orig-backup", "imager",
)


class LiveTab(QWidget):
    """Live view over WiFi (/v1/getCameraImage + /v1/maxdB) plus Auto-Capture.

    The device exposes no live spectrum/peak over any interface, so the live
    image shows only the camera + dB. With Auto-Capture on, the moment you save
    a measurement on the device it is downloaded and the full analysis (exact
    peak + spectrum bars) is shown below — the closest to live the device allows.
    """

    def __init__(self, host: str = "192.168.1.100") -> None:  # placeholder; editable in the UI
        super().__init__()
        self._worker: HttpLiveWorker | None = None
        self._last: QImage | None = None
        self._last_peak: float | None = None  # kHz from the last saved capture

        self._host = QLineEdit(host)
        self._start = QPushButton("Start")
        self._snap = QPushButton("Snapshot")
        self._snap.setEnabled(False)
        self._auto = QCheckBox("Auto-Capture")
        self._auto.setChecked(True)
        self._auto.setToolTip("Beim Speichern am Gerät automatisch die volle "
                              "Analyse (Peak + Balken) laden")
        self._start.clicked.connect(self._toggle)
        self._snap.clicked.connect(self._snapshot)

        self._view = QLabel("Live über WLAN — Kamera muss wach sein (Display an)")
        self._view.setAlignment(Qt.AlignCenter)
        self._view.setMinimumSize(480, 270)
        self._view.setStyleSheet("background:#111; color:#888;")

        self._fig = Figure(figsize=(7, 5), tight_layout=True)
        self._canvas = FigureCanvas(self._fig)
        self._cap_hint = QLabel("Auto-Capture aktiv — am Gerät speichern, "
                                "dann erscheint hier die Analyse mit exaktem Peak.")
        self._cap_hint.setAlignment(Qt.AlignCenter)
        self._cap_hint.setStyleSheet("color:#888;")

        top = QHBoxLayout()
        top.addWidget(QLabel("Kamera-IP:"))
        top.addWidget(self._host, 1)
        top.addWidget(self._auto)
        top.addWidget(self._start)
        top.addWidget(self._snap)

        analysis = QVBoxLayout()
        analysis.addWidget(self._cap_hint)
        analysis.addWidget(self._canvas, 1)
        analysis_w = QWidget()
        analysis_w.setLayout(analysis)

        split = QSplitter(Qt.Vertical)
        split.addWidget(self._view)
        split.addWidget(analysis_w)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 460])

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(split, 1)

    def _toggle(self) -> None:
        if self._worker:
            self._stop()
            return
        watch = _WATCH_ROOT if self._auto.isChecked() else None
        self._worker = HttpLiveWorker(self._host.text().strip(), watch_root=watch)
        self._worker.frame.connect(self._on_frame)
        self._worker.captured.connect(self._on_capture)
        self._worker.status.connect(
            lambda s: self.window().statusBar().showMessage(s, 4000)
        )
        self._worker.start()
        self._start.setText("Stop")
        self._snap.setEnabled(True)

    def _stop(self) -> None:
        if self._worker:
            self._worker.stop()
            self._worker.wait(3000)
            self._worker = None
        self._start.setText("Start")
        self._snap.setEnabled(False)

    @staticmethod
    def _text(painter: QPainter, x: int, y: int, s: str) -> None:
        """White text with a black shadow so it stays legible on any image."""
        painter.setPen(QColor(0, 0, 0, 200))
        painter.drawText(x + 2, y + 2, s)
        painter.setPen(QColor("white"))
        painter.drawText(x, y, s)

    def _on_frame(self, img: QImage, info: dict) -> None:
        self._last = img
        pm = QPixmap.fromImage(img)
        w, h = pm.width(), pm.height()
        px = int(min(max(info["nx"], 0.0), 1.0) * w)
        py = int(min(max(info["ny"], 0.0), 1.0) * h)
        painter = QPainter(pm)

        pen = QPen(QColor("#ffd23f"))
        pen.setWidth(max(2, h // 240))
        painter.setPen(pen)
        r = max(12, h // 26)
        painter.drawEllipse(px - r, py - r, 2 * r, 2 * r)

        font = painter.font()
        font.setPointSize(max(15, h // 22))
        font.setBold(True)
        painter.setFont(font)
        self._text(painter, px + r + 10, py + 6, f"{info['db']:.1f} dB")

        # peak from the last saved capture (no live peak exists on the device)
        if self._last_peak is not None:
            font.setPointSize(max(13, h // 30))
            painter.setFont(font)
            self._text(painter, 18, max(30, h // 24),
                       f"Letzter Peak: {self._last_peak:.1f} kHz")
        painter.end()

        self._view.setPixmap(
            pm.scaled(self._view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def _on_capture(self, folder: str) -> None:
        try:
            m = formats.load_measurement(folder)
        except Exception as exc:
            self.window().statusBar().showMessage(f"Analyse-Fehler: {exc}", 6000)
            return
        self._last_peak = m.dominant_khz
        self._cap_hint.setText(
            f"Aufnahme {m.name} — Peak {m.dominant_khz:.1f} kHz, max {m.max_db:.1f} dB"
        )
        analysis_figure(m, self._fig)
        self._canvas.draw_idle()

    def _snapshot(self) -> None:
        if self._last is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Snapshot speichern", "live.jpg", "Images (*.jpg *.png)"
        )
        if path and self._last.save(path):
            self.window().statusBar().showMessage(f"Gespeichert: {path}", 5000)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._stop()
        super().closeEvent(event)


class FilesTab(QWidget):
    """Browse and download the device storage over adb."""

    def __init__(self) -> None:
        super().__init__()
        self._serial: str | None = None
        self._worker: Worker | None = None

        self._status = QLabel("…")
        self._path = QLineEdit("/sdcard/")
        up = QPushButton("⬆ Up")
        go = QPushButton("List")
        self._list = QListWidget()
        download = QPushButton("⬇ Download selected…")
        connect = QPushButton("Reconnect")

        connect.clicked.connect(self._refresh_device)
        go.clicked.connect(self._list_dir)
        up.clicked.connect(self._go_up)
        download.clicked.connect(self._download)
        self._path.returnPressed.connect(self._list_dir)
        self._list.itemDoubleClicked.connect(self._open_item)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Path:"))
        bar.addWidget(self._path, 1)
        bar.addWidget(up)
        bar.addWidget(go)

        layout = QVBoxLayout(self)
        layout.addWidget(self._status)
        layout.addLayout(bar)
        layout.addWidget(self._list, 1)
        layout.addWidget(download)
        self._refresh_device()

    def _refresh_device(self) -> None:
        mode = device.usb_mode()
        self._serial = device.first_ready_serial()
        if self._serial:
            self._status.setText(f"✅ Connected (adb: {self._serial}, USB mode: {mode})")
            self._list_dir()
        elif mode == "live":
            self._status.setText(
                "⚠ Camera is in WEBCAM mode — switch it to file/USB mode on the "
                "touchscreen to browse files."
            )
        elif mode == "file":
            self._status.setText(
                "⚠ Rockchip device seen but adb not ready — confirm the USB-debug / "
                "file prompt on the camera screen, then click Reconnect."
            )
        else:
            self._status.setText("✖ No SonoCam detected on USB.")

    def _busy(self, fn, *args, then=None) -> None:
        self._worker = Worker(fn, *args)
        if then:
            self._worker.done.connect(then)
        self._worker.failed.connect(
            lambda msg: self.window().statusBar().showMessage(msg, 8000)
        )
        self._worker.start()

    def _list_dir(self) -> None:
        if not self._serial:
            return
        path = self._path.text().strip() or "/"
        self._list.clear()
        self._list.addItem("…loading…")
        self._busy(device.listdir, path, self._serial, then=self._show_entries)

    def _show_entries(self, entries) -> None:
        self._list.clear()
        for e in entries:
            label = f"📁 {e.name}" if e.is_dir else f"📄 {e.name}   ({e.size:,} B)"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, e)
            self._list.addItem(item)

    def _open_item(self, item: QListWidgetItem) -> None:
        e = item.data(Qt.UserRole)
        if e and e.is_dir:
            self._path.setText(e.path)
            self._list_dir()

    def _go_up(self) -> None:
        cur = self._path.text().rstrip("/")
        parent = os.path.dirname(cur) or "/"
        self._path.setText(parent)
        self._list_dir()

    def _download(self) -> None:
        if not self._serial:
            return
        items = self._list.selectedItems()
        targets = [it.data(Qt.UserRole) for it in items if it.data(Qt.UserRole)]
        if not targets:
            return
        local = QFileDialog.getExistingDirectory(self, "Download to…")
        if not local:
            return
        self.window().statusBar().showMessage(f"Downloading {len(targets)} item(s)…")
        for e in targets:
            self._busy(
                device.pull, e.path, local, self._serial,
                then=lambda out: self.window().statusBar().showMessage(out, 6000),
            )


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("iSoCam — CRY8024")
        self.resize(960, 640)
        from .measurements import MeasurementsTab
        tabs = QTabWidget()
        tabs.addTab(MeasurementsTab(), "Messungen")
        tabs.addTab(LiveTab(), "Live")
        tabs.addTab(FilesTab(), "Files")
        self.setCentralWidget(tabs)
        self.statusBar().showMessage("Ready")
