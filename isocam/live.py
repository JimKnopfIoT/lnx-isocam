"""Live MJPEG capture from the camera's UVC webcam personality.

QtMultimedia is unavailable on this install (no multimedia backend plugins) and
the system ffmpeg binary is broken, so we capture via the known-good `v4l2-ctl`:
it streams a continuous concatenation of MJPEG frames into a FIFO, and we split
them on JPEG SOI/EOI markers and hand each frame to Qt as a QImage.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import subprocess
import tempfile

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from .webclient import CameraHTTP

_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"


class HttpLiveWorker(QThread):
    """Poll the camera's WiFi HTTP API for live frames + loudness + band.

    The UVC personality (/dev/video0) only mirrors the device screen and is
    often black; the reliable live image comes from /v1/getCameraImage. Uses one
    persistent HTTP connection and no idle delay — the camera WiFi (~230 ms per
    ~440 KB frame) is the limit, so we run as fast as it allows (~4 fps).
    """

    # info dict: {"db", "nx", "ny", "band": (low_khz, high_khz)}
    frame = Signal(QImage, object)
    captured = Signal(str)   # local folder of a newly saved measurement
    status = Signal(str)

    def __init__(self, host: str, watch_root: str | None = None) -> None:
        super().__init__()
        self._host = host
        self._watch_root = watch_root  # if set, auto-download new saved measurements
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def _get(self, conn, path: str) -> bytes:
        conn.request("GET", path)
        r = conn.getresponse()
        body = r.read()
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        return body

    def _check_captures(self, cam: CameraHTTP, known: set) -> None:
        """Download measurements saved on the device since we started watching."""
        try:
            cur = {n for n in cam.listdir("imager/Default") if n[:8].isdigit()}
        except OSError:
            return
        for ts in sorted(cur - known):
            local = os.path.join(self._watch_root, "Default", ts)
            try:
                for f in cam.listdir(f"imager/Default/{ts}"):
                    cam.download(f"imager/Default/{ts}/{f}", os.path.join(local, f))
                known.add(ts)
                self.captured.emit(local)
            except OSError as exc:
                self.status.emit(f"Capture-Download fehlgeschlagen: {exc}")

    def run(self) -> None:
        cam = CameraHTTP(self._host)
        try:
            token = cam.login()
        except Exception as exc:
            self.status.emit(f"Login fehlgeschlagen ({self._host}): {exc}")
            return
        self.status.emit(f"verbunden mit {self._host}")
        conn = http.client.HTTPConnection(self._host, 9999, timeout=8)
        band = (0.0, 0.0)
        known: set | None = None
        if self._watch_root is not None:
            try:
                known = {n for n in cam.listdir("imager/Default") if n[:8].isdigit()}
            except OSError:
                known = set()
        i = 0
        while not self._stop:
            try:
                img_bytes = self._get(conn, f"/v1/getCameraImage?userId={token}")
                peaks = json.loads(
                    self._get(conn, f"/v1/maxdB?userId={token}")
                ).get("data", [])
                if i % 20 == 0:  # band changes rarely; poll occasionally
                    fb = json.loads(
                        self._get(conn, f"/v1/frequency?userId={token}")
                    ).get("data", [])
                    en = next((d for d in fb if d.get("enable")), None)
                    if en:
                        band = (en["freqDown"] / 1000.0, en["freqUp"] / 1000.0)
                if known is not None and i % 12 == 0:  # watch for new captures
                    self._check_captures(cam, known)
                i += 1
                img = QImage.fromData(img_bytes, "JPG")
                loud = max(peaks, key=lambda p: p.get("value", 0.0)) if peaks else {}
                info = {
                    "db": float(loud.get("value", 0.0)),
                    "nx": loud.get("x", 16) / 31.0,
                    "ny": loud.get("y", 12) / 23.0,
                    "band": band,
                }
                if not img.isNull():
                    self.frame.emit(img, info)
                self.msleep(10)  # yield
            except Exception as exc:
                self.status.emit(f"neu verbinden… ({exc})")
                try:
                    conn.close()
                except OSError:
                    pass
                conn = http.client.HTTPConnection(self._host, 9999, timeout=8)
                self.msleep(500)
        conn.close()


# The SonoCam's UVC capture bridge always enumerates with this USB id.
SONOCAM_VID = "eba4"
SONOCAM_PID = "7588"


def _usb_ident(node: str) -> tuple[str, str, str]:
    """(vendor_id, model_id, serial) for a /dev/videoN via udev properties."""
    try:
        out = subprocess.run(
            ["udevadm", "info", "--query=property", "--name", node],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ("", "", "")
    props = dict(
        line.split("=", 1) for line in out.splitlines() if "=" in line
    )
    return (
        props.get("ID_VENDOR_ID", ""),
        props.get("ID_MODEL_ID", ""),
        props.get("ID_SERIAL_SHORT", ""),
    )


def video_inputs() -> list[tuple[str, str]]:
    """Return [(/dev/videoN, label), ...]; the SonoCam is identified by its USB
    id (eba4:7588) and sorted first so the Live view never grabs the wrong cam."""
    try:
        out = subprocess.run(
            ["v4l2-ctl", "--list-devices"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    inputs: list[tuple[str, str, bool]] = []
    card = "camera"
    for line in out.splitlines():
        if not line.strip():
            continue
        if not line.startswith(("\t", " ")):
            card = line.split("(")[0].strip().rstrip(":")
            continue
        node = line.strip()
        if not (re.fullmatch(r"/dev/video\d+", node) and _supports_capture(node)):
            continue
        vid, pid, serial = _usb_ident(node)
        is_sono = (vid, pid) == (SONOCAM_VID, SONOCAM_PID)
        label = (f"SonoCam [{serial}] ({node})" if is_sono
                 else f"{card} ({node})")
        inputs.append((node, label, is_sono))
    inputs.sort(key=lambda t: (not t[2], t[0]))  # SonoCam first
    return [(node, label) for node, label, _ in inputs]


def _supports_capture(node: str) -> bool:
    try:
        out = subprocess.run(
            ["v4l2-ctl", "-d", node, "--list-formats"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        return "MJPG" in out or "YUYV" in out
    except (OSError, subprocess.SubprocessError):
        return False


class CaptureWorker(QThread):
    """Stream MJPEG frames from a V4L2 node and emit them as QImage."""

    frame = Signal(QImage)
    error = Signal(str)

    def __init__(self, device: str, width: int = 1920, height: int = 1080) -> None:
        super().__init__()
        self._device = device
        self._w = width
        self._h = height
        self._proc: subprocess.Popen | None = None
        self._stop = False

    def stop(self) -> None:
        self._stop = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def run(self) -> None:  # QThread entry point
        tmpdir = tempfile.mkdtemp(prefix="isocam_")
        fifo = os.path.join(tmpdir, "stream.mjpg")
        try:
            os.mkfifo(fifo)
            self._proc = subprocess.Popen(
                [
                    "v4l2-ctl", "-d", self._device,
                    f"--set-fmt-video=width={self._w},height={self._h},pixelformat=MJPG",
                    "--stream-mmap", "--stream-count=0", f"--stream-to={fifo}",
                ],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self._read_fifo(fifo)
        except OSError as exc:
            self.error.emit(str(exc))
        finally:
            self.stop()
            try:
                os.remove(fifo)
                os.rmdir(tmpdir)
            except OSError:
                pass

    def _read_fifo(self, fifo: str) -> None:
        buf = b""
        with open(fifo, "rb") as stream:
            while not self._stop:
                chunk = stream.read(65536)
                if not chunk:
                    break
                buf += chunk
                buf = self._drain(buf)

    def _drain(self, buf: bytes) -> bytes:
        while True:
            i = buf.find(_SOI)
            if i < 0:
                return buf[-1:]  # keep a trailing byte in case of split marker
            j = buf.find(_EOI, i + 2)
            if j < 0:
                return buf[i:]
            img = QImage.fromData(buf[i:j + 2], "JPG")
            if not img.isNull():
                self.frame.emit(img)
            buf = buf[j + 2:]
