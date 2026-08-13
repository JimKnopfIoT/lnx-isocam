"""Measurements tab — the main image-analysis view.

Brings camera photo + acoustic source overlay + peak markers together with the
frequency spectrum/band in one combined frame, ready to screenshot or export.
Works fully offline against a local folder (e.g. orig-backup/imager).
"""

from __future__ import annotations

import os
import shutil

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import formats
from ..render import analysis_figure
from ..webclient import CameraHTTP
from ..worker import Worker

DEFAULT_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "orig-backup", "imager",
)


class MeasurementsTab(QWidget):
    def __init__(self, root: str = DEFAULT_ROOT) -> None:
        super().__init__()
        self._root = root
        self._m: formats.Measurement | None = None

        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._select)
        open_btn = QPushButton("Ordner…")
        open_btn.clicked.connect(self._choose_root)
        del_btn = QPushButton("Messung löschen")
        del_btn.clicked.connect(self._delete)
        self._host = QLineEdit("192.168.1.100")
        sync_btn = QPushButton("⟳ Von Kamera laden")
        sync_btn.clicked.connect(self._sync)

        sync_row = QHBoxLayout()
        sync_row.addWidget(self._host, 1)
        sync_row.addWidget(sync_btn)

        left = QVBoxLayout()
        left.addWidget(open_btn)
        left.addLayout(sync_row)
        left.addWidget(self._list, 1)
        left.addWidget(del_btn)
        left_w = QWidget()
        left_w.setLayout(left)

        self._fig = Figure(figsize=(8, 7), tight_layout=True)
        self._canvas = FigureCanvas(self._fig)

        self._alpha = QSlider(Qt.Horizontal)
        self._alpha.setRange(0, 100)
        self._alpha.setValue(100)
        self._alpha.valueChanged.connect(self._redraw)
        self._markers = QCheckBox("Peak-Marker")
        self._markers.setChecked(True)
        self._markers.toggled.connect(self._redraw)
        export = QPushButton("Export PNG…")
        export.clicked.connect(self._export)
        export_all = QPushButton("Alle exportieren…")
        export_all.clicked.connect(self._export_all)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Overlay:"))
        controls.addWidget(self._alpha)
        controls.addWidget(self._markers)
        controls.addStretch(1)
        controls.addWidget(export)
        controls.addWidget(export_all)

        right = QVBoxLayout()
        right.addWidget(self._canvas, 1)
        right.addLayout(controls)
        right_w = QWidget()
        right_w.setLayout(right)

        split = QSplitter()
        split.addWidget(left_w)
        split.addWidget(right_w)
        split.setStretchFactor(1, 1)
        split.setSizes([220, 820])

        layout = QVBoxLayout(self)
        layout.addWidget(split)
        self.reload()

    # --- data ---
    def reload(self) -> None:
        self._list.clear()
        self._folders = (
            formats.find_measurements(self._root) if os.path.isdir(self._root) else []
        )
        for f in self._folders:
            self._list.addItem(os.path.basename(f))
        if self._folders:
            self._list.setCurrentRow(len(self._folders) - 1)

    def _sync(self) -> None:
        """Download measurements present on the camera but not yet local."""
        host = self._host.text().strip()
        root = self._root

        def work():
            cam = CameraHTTP(host, timeout=15)
            remote = [n for n in cam.listdir("imager/Default") if n[:8].isdigit()]
            local_dir = os.path.join(root, "Default")
            have = set(os.listdir(local_dir)) if os.path.isdir(local_dir) else set()
            todo = [n for n in remote if n not in have]
            for ts in todo:
                for f in cam.listdir(f"imager/Default/{ts}"):
                    cam.download(
                        f"imager/Default/{ts}/{f}",
                        os.path.join(local_dir, ts, f),
                    )
            return len(todo), len(remote)

        self.window().statusBar().showMessage(f"Lade von {host} …")
        self._sync_worker = Worker(work)
        self._sync_worker.done.connect(self._sync_done)
        self._sync_worker.failed.connect(
            lambda e: self.window().statusBar().showMessage(f"Sync-Fehler: {e}", 8000)
        )
        self._sync_worker.start()

    def _sync_done(self, result) -> None:
        new, total = result
        self.window().statusBar().showMessage(
            f"{new} neue Messungen geladen ({total} auf Kamera)", 6000
        )
        self.reload()

    def _choose_root(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Messungs-Ordner", self._root)
        if d:
            self._root = d
            self.reload()

    def _select(self, row: int) -> None:
        if row < 0 or row >= len(self._folders):
            return
        try:
            self._m = formats.load_measurement(self._folders[row])
        except Exception as exc:
            self.window().statusBar().showMessage(f"Fehler: {exc}", 8000)
            return
        self._redraw()

    def _redraw(self) -> None:
        if not self._m:
            return
        analysis_figure(
            self._m, self._fig,
            alpha=self._alpha.value() / 100.0,
            markers=self._markers.isChecked(),
        )
        self._canvas.draw_idle()

    # --- export ---
    def _export(self) -> None:
        if not self._m:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Analyse exportieren", f"{self._m.name}.png", "PNG (*.png)"
        )
        if path:
            self._fig.savefig(path, dpi=150)
            self.window().statusBar().showMessage(f"Gespeichert: {path}", 6000)

    def _delete(self) -> None:
        row = self._list.currentRow()
        if row < 0 or row >= len(self._folders):
            return
        folder = self._folders[row]
        n = len(os.listdir(folder)) if os.path.isdir(folder) else 0
        resp = QMessageBox.question(
            self, "Messung löschen",
            f"Den kompletten Messordner unwiderruflich löschen?\n\n"
            f"{folder}\n({n} Dateien: Foto, Overlay, .doa, .wav, info.json …)",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
        try:
            shutil.rmtree(folder)
        except OSError as exc:
            self.window().statusBar().showMessage(f"Löschen fehlgeschlagen: {exc}", 8000)
            return
        self.window().statusBar().showMessage(f"Gelöscht: {folder}", 6000)
        self.reload()

    def _export_all(self) -> None:
        if not self._folders:
            return
        out = QFileDialog.getExistingDirectory(self, "Export-Ordner")
        if not out:
            return
        fig = Figure(figsize=(8, 7), tight_layout=True)
        FigureCanvas(fig)  # gives the figure a canvas so savefig works headless
        n = 0
        for folder in self._folders:
            try:
                m = formats.load_measurement(folder)
                analysis_figure(m, fig, alpha=1.0, markers=self._markers.isChecked())
                fig.savefig(os.path.join(out, f"{m.name}.png"), dpi=150)
                n += 1
            except Exception:
                continue
        self.window().statusBar().showMessage(f"{n} Analysen exportiert → {out}", 8000)
