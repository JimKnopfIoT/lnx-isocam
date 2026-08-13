#!/usr/bin/env python3
"""Calibrate the spectrum frequency axis with known tones.

Play a known pure tone (frequency generator + speaker) at the camera, save the
measurement on the device, then pass the measurement folder and the known kHz:

  python3 tools/calibrate_freq.py \
      orig-backup/imager/Default/<ts1>=10 \
      orig-backup/imager/Default/<ts2>=15 \
      orig-backup/imager/Default/<ts3>=19

It finds the PowerSpec peak bin of each tone, fits bin->kHz (linear, since the
axis is linear) and writes freq_calib.json, which formats.py then uses.
Two or more points recommended; one point assumes zero offset.
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from isocam import formats  # noqa: E402

CALIB = formats._CALIB_PATH


def peak_bin(folder: str) -> tuple[int, int]:
    m = formats.load_measurement(folder)
    if m.spectrum is None:
        raise ValueError(f"no spectrum in {folder}")
    spec = m.spectrum
    n = spec.size
    lo, hi = 2, int(n * 0.98)  # skip DC and the Nyquist-edge artefact
    return lo + int(np.argmax(spec[lo:hi])), n


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    pts = []
    n_ref = 0
    for arg in sys.argv[1:]:
        folder, khz = arg.rsplit("=", 1)
        b, n = peak_bin(folder)
        n_ref = n
        pts.append((b, float(khz)))
        print(f"  {os.path.basename(folder)}: peak bin {b}/{n}  -> {float(khz)} kHz")

    bins = np.array([p[0] for p in pts], float)
    khz = np.array([p[1] for p in pts], float)
    if len(pts) >= 2:
        slope, offset = np.polyfit(bins, khz, 1)
    else:
        slope, offset = khz[0] / bins[0], 0.0

    out = {
        "slope_khz_per_bin": float(slope),
        "offset_khz": float(offset),
        "n": int(n_ref),
        "points": [[int(b), float(k)] for b, k in pts],
    }
    json.dump(out, open(CALIB, "w"), indent=2)
    print(f"\nfit:  kHz = {slope:.6f} * bin + {offset:.4f}   (n={n_ref})")
    # residuals
    for b, k in pts:
        print(f"   bin {b}: known {k:.2f}  fitted {slope*b+offset:.2f} kHz")
    print(f"\nwrote {CALIB}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
