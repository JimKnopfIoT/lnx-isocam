"""Device access for the SonoCam Pocket CRY8024.

The camera exposes two non-simultaneous USB personalities:
  * file mode  -> Rockchip RK3576 ADB gadget   (USB id 2207:xxxx)
  * live mode  -> UVC webcam / HDMI bridge      (USB id eba4:7588)

File access goes through the `adb` binary (no python dependency needed).
This module is format-independent: it lists, pulls and pushes files; parsing
of the recording formats lives in ``formats.py``.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

ADB = "adb"

# USB ids seen on this device.
USB_FILE_VENDOR = "2207"          # Rockchip (rk3576_u ADB gadget)
USB_LIVE_ID = ("eba4", "7588")    # UVC capture bridge


class AdbError(RuntimeError):
    """Raised when an adb command fails."""


@dataclass(frozen=True)
class RemoteEntry:
    name: str
    path: str
    is_dir: bool
    size: int  # bytes; 0 for directories


def _run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [ADB, *args], capture_output=True, text=True, timeout=timeout
    )


# --- mode / presence -------------------------------------------------------

def usb_mode() -> str | None:
    """Return 'file', 'live' or None based on what is plugged in right now.

    Uses lsusb so it works even before adb has authorised the device.
    """
    try:
        out = subprocess.run(
            ["lsusb"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if f"{USB_LIVE_ID[0]}:{USB_LIVE_ID[1]}" in out:
        return "live"
    if re.search(rf"\b{USB_FILE_VENDOR}:[0-9a-f]{{4}}\b", out):
        return "file"
    return None


def devices() -> list[tuple[str, str]]:
    """Return [(serial, state), ...] from `adb devices`. state e.g. 'device'."""
    try:
        r = _run(["devices"])
    except (OSError, subprocess.SubprocessError) as exc:
        raise AdbError(str(exc)) from exc
    result = []
    for line in r.stdout.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            result.append((parts[0], parts[1]))
    return result


def first_ready_serial() -> str | None:
    for serial, state in devices():
        if state == "device":
            return serial
    return None


# --- filesystem ------------------------------------------------------------

def _shell_args(cmd: str, serial: str | None) -> list[str]:
    return (["-s", serial] if serial else []) + ["shell", cmd]


def shell(cmd: str, serial: str | None = None) -> str:
    r = _run(_shell_args(cmd, serial))
    if r.returncode != 0:
        raise AdbError(r.stderr.strip() or r.stdout.strip() or "adb shell failed")
    return r.stdout


def listdir(path: str, serial: str | None = None) -> list[RemoteEntry]:
    """List a remote directory. Robust against busybox/toybox `ls` variants."""
    path = path.rstrip("/") or "/"
    # -F appends '/' to dirs and '*' to executables; -1 = one per line.
    out = shell(f"ls -1AF {_q(path)}", serial)
    entries: list[RemoteEntry] = []
    for raw in out.splitlines():
        name = raw.strip()
        if not name or name in (".", ".."):
            continue
        is_dir = name.endswith("/")
        clean = name.rstrip("*/@=|")
        full = f"{path}/{clean}" if path != "/" else f"/{clean}"
        size = 0
        if not is_dir:
            size = _stat_size(full, serial)
        entries.append(RemoteEntry(clean, full, is_dir, size))
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return entries


def _stat_size(path: str, serial: str | None) -> int:
    try:
        out = shell(f"stat -c %s {_q(path)} 2>/dev/null", serial).strip()
        return int(out) if out.isdigit() else 0
    except (AdbError, ValueError):
        return 0


def pull(remote: str, local_dir: str, serial: str | None = None) -> str:
    args = (["-s", serial] if serial else []) + ["pull", remote, local_dir]
    r = _run(args, timeout=600)
    if r.returncode != 0:
        raise AdbError(r.stderr.strip() or r.stdout.strip() or "adb pull failed")
    return r.stdout.strip()


def push(local: str, remote: str, serial: str | None = None) -> str:
    args = (["-s", serial] if serial else []) + ["push", local, remote]
    r = _run(args, timeout=600)
    if r.returncode != 0:
        raise AdbError(r.stderr.strip() or r.stdout.strip() or "adb push failed")
    return r.stdout.strip()


def _q(path: str) -> str:
    """Quote a remote path for the device shell."""
    return "'" + path.replace("'", "'\\''") + "'"
