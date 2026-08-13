"""Parsers for SonoCam Pocket CRY8024 measurement files.

A measurement lives in one folder ``imager/<album>/<YYYYMMDD_HHMMSS>/`` with:
  <ts>_1.jpg       optical camera photo (e.g. 1280x720 RGB)
  <ts>_0.jpg       acoustic overlay (PNG-in-jpg, RGBA, transparent except source)
  <ts>.doa         hybrid text+binary: FILE_INFO + STRUCT_INFO + data blocks
  <ts>.wav         ultrasonic audio (mono 16-bit ~130 kHz)
  info.json        full metadata (frequency band, dB peaks, calibration, …)
  .thumbnail.jpg, dateTime (epoch ms), scenarioMode

The .doa data region (between ``--start--`` and ``--end--``) is a sequence of
``**BlockName:`` entries. Scalars are ASCII (`maxSpl:<value> <x> <y>`); the big
arrays are length-prefixed float32-LE: ``PowerSpec:<n>\r\n<floats>`` and
``ResEngMap:<rows>x<cols>\r\n<floats>``. Per-peak frequency is NOT stored — each
detected source carries position + dB only; frequency is the global band.
"""

from __future__ import annotations

import json
import os
import re
import struct
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

_MARK = re.compile(rb"\*\*([A-Za-z0-9_]+):")


@dataclass
class Peak:
    x: int
    y: int
    db: float
    nx: float = 0.5  # normalized 0..1 position in the image (x -> width)
    ny: float = 0.5  # normalized 0..1 position in the image (y -> height)


@dataclass
class Measurement:
    folder: str
    name: str
    info: dict
    photo_path: str | None
    overlay_path: str | None
    peaks: list[Peak] = field(default_factory=list)
    freq_low_khz: float = 0.0
    freq_high_khz: float = 0.0
    res_row: int = 32   # grid steps along image width  (x: 0..res_row-1)
    res_col: int = 24   # grid steps along image height (y: 0..res_col-1)
    heatmap: np.ndarray | None = None          # ResEngMap, shape (rows, cols)
    spectrum: np.ndarray | None = None         # PowerSpec magnitudes
    spectrum_hz: np.ndarray | None = None      # matching frequency axis (Hz)
    dominant_khz: float = 0.0
    max_db: float = 0.0

    def top_peaks(self, n: int = 3) -> list[Peak]:
        return sorted(self.peaks, key=lambda p: p.db, reverse=True)[:n]

    def composite(self, alpha: float = 1.0) -> Image.Image:
        """Camera photo with the acoustic overlay composited on top."""
        if not self.photo_path:
            raise FileNotFoundError("no photo in measurement")
        photo = Image.open(self.photo_path).convert("RGBA")
        if not self.overlay_path:
            return photo
        ov = Image.open(self.overlay_path).convert("RGBA").resize(
            photo.size, Image.BILINEAR
        )
        if alpha < 1.0:
            a = ov.getchannel("A").point(lambda v: int(v * alpha))
            ov.putalpha(a)
        return Image.alpha_composite(photo, ov)


def _find_file(folder: str, suffix: str) -> str | None:
    for fn in os.listdir(folder):
        if fn.endswith(suffix):
            return os.path.join(folder, fn)
    return None


# Length-prefixed float32 arrays — consume their exact byte length so random
# 0x2A2A ("**") bytes inside the binary are not mistaken for block markers.
_BIN_BLOCKS = {"PowerSpec", "ResEngMap"}


def _doa_blocks(path: str) -> dict[str, bytes]:
    data = open(path, "rb").read()
    s = data.find(b"--start--")
    e = data.rfind(b"--end--")
    if s < 0 or e < 0:
        return {}
    region = data[s + len(b"--start--"): e]
    blocks: dict[str, bytes] = {}
    i = 0
    while i < len(region):
        mk = region.find(b"**", i)
        if mk < 0:
            break
        colon = region.find(b":", mk)
        if colon < 0:
            break
        name = region[mk + 2:colon].decode("latin1")
        nl = region.find(b"\r\n", colon)
        if name in _BIN_BLOCKS and nl > 0:
            head = region[colon + 1:nl]
            try:
                if b"x" in head:
                    r, c = (int(v) for v in head.split(b"x"))
                    n = r * c
                else:
                    n = int(head)
            except ValueError:
                n = 0
            binend = nl + 2 + n * 4
            blocks[name] = region[colon + 1:binend]  # header + raw floats
            i = binend
        else:
            nxt = region.find(b"**", colon)
            end = nxt if nxt > 0 else len(region)
            blocks[name] = region[colon + 1:end]
            i = end
    return blocks


def _parse_float_array(block: bytes) -> tuple[str, np.ndarray]:
    """Blocks like b'3201\\r\\n<float32…>' or b'32x24\\r\\n<float32…>'."""
    head, _, raw = block.partition(b"\r\n")
    n = 0
    if b"x" in head:
        rows, cols = (int(v) for v in head.split(b"x"))
        n = rows * cols
    elif head.strip().isdigit():
        n = int(head)
    with np.errstate(invalid="ignore"):
        arr = np.frombuffer(raw[: n * 4], dtype="<f4").astype(np.float64)
    return head.decode(), arr


def _mask_invalid(arr: np.ndarray, limit: float = 1e6) -> np.ndarray:
    """Replace sentinel/invalid cells (non-finite or absurd) with NaN."""
    bad = ~np.isfinite(arr) | (np.abs(arr) > limit)
    out = arr.copy()
    out[bad] = np.nan
    return out


def _doa_fileinfo(path: str) -> dict[str, str]:
    data = open(path, "rb").read(512)
    e = data.find(b"==FILE_INFO_END==")
    txt = data[:e].decode("latin1") if e > 0 else ""
    return dict(re.findall(r"([A-Z_]+):([^\r\n]+)", txt))


def load_measurement(folder: str) -> Measurement:
    name = os.path.basename(folder.rstrip("/"))
    info_path = os.path.join(folder, "info.json")
    info = json.load(open(info_path)) if os.path.exists(info_path) else {}

    m = Measurement(
        folder=folder,
        name=name,
        info=info,
        photo_path=_find_file(folder, "_1.jpg"),
        overlay_path=_find_file(folder, "_0.jpg"),
        freq_low_khz=float(info.get("frequencyDown", 0.0)),
        freq_high_khz=float(info.get("frequencyUp", 0.0)),
        max_db=float(info.get("maxdBSpl", 0.0)),
    )

    doa = _find_file(folder, ".doa")
    if doa:
        fi = _doa_fileinfo(doa)
        m.res_row = int(fi.get("RESROW", 32) or 32)
        m.res_col = int(fi.get("RESCOL", 24) or 24)

    # peaks: prefer info.json doaPeaksList (carries dB). x maps to width, y to
    # height (convention verified against the acoustic overlay centroid).
    for group in info.get("doaPeaksList", []) or []:
        for p in group:
            x, y = int(p["x"]), int(p["y"])
            m.peaks.append(Peak(
                x, y, float(p["db"]),
                nx=x / max(1, m.res_row - 1),
                ny=y / max(1, m.res_col - 1),
            ))

    if doa:
        blocks = _doa_blocks(doa)
        if "ResEngMap" in blocks:
            head, arr = _parse_float_array(blocks["ResEngMap"])
            try:
                rows, cols = (int(v) for v in head.split("x"))
                if arr.size >= rows * cols:
                    m.heatmap = _mask_invalid(arr[: rows * cols]).reshape(rows, cols)
            except ValueError:
                pass
        if "PowerSpec" in blocks:
            _, spec = _parse_float_array(blocks["PowerSpec"])
            spec = np.nan_to_num(spec, nan=0.0, posinf=0.0, neginf=0.0)
            m.spectrum = spec
            if spec.size > 1:
                m.spectrum_hz = spectrum_axis_khz(spec.size) * 1000.0
                m.dominant_khz = _dominant_in_band(m)
    return m


# --- frequency-axis calibration -------------------------------------------
# bin index -> kHz is linear: khz = slope*bin + offset. Defaults map 0..65 kHz;
# a real fit (frequency generator + speaker) is written to freq_calib.json by
# tools/calibrate_freq.py.
_CALIB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "freq_calib.json"
)


def freq_calibration() -> tuple[float, float, int]:
    """Return (slope_khz_per_bin, offset_khz, ref_n). ref_n=0 means 'any n'."""
    if os.path.exists(_CALIB_PATH):
        try:
            c = json.load(open(_CALIB_PATH))
            return float(c["slope_khz_per_bin"]), float(c["offset_khz"]), int(c.get("n", 0))
        except (ValueError, KeyError, OSError):
            pass
    return 65.0 / 3200.0, 0.0, 3201  # uncalibrated default: 0..65 kHz over 3201 bins


def spectrum_axis_khz(n: int) -> np.ndarray:
    slope, offset, ref_n = freq_calibration()
    if ref_n and ref_n != n:  # scale slope if a different bin count appears
        slope = slope * (ref_n - 1) / (n - 1)
    return np.arange(n) * slope + offset


def _dominant_in_band(m: Measurement) -> float:
    """Peak frequency within the active band (matches the device readout)."""
    khz = m.spectrum_hz / 1000.0
    spec = m.spectrum.copy()
    if not m.info.get("autoFreqEnable") and m.freq_high_khz > m.freq_low_khz:
        in_band = (khz >= m.freq_low_khz) & (khz <= m.freq_high_khz)
    else:
        in_band = khz < khz.max() * 0.99  # drop Nyquist-edge artefact
    if not in_band.any():
        return 0.0
    idx = np.where(in_band)[0]
    return float(khz[idx[np.argmax(spec[idx])]])


def find_measurements(root: str) -> list[str]:
    """Return measurement folders under an imager/<album> tree (or any root)."""
    found = []
    for dirpath, _dirs, files in os.walk(root):
        if any(f == "info.json" for f in files) and any(
            f.endswith(".doa") for f in files
        ):
            found.append(dirpath)
    return sorted(found)
