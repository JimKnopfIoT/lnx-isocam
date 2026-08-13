"""Render a measurement into one combined analysis frame.

Camera photo + acoustic source overlay with the loudness (dB) and dominant
frequency labelled at the spot, plus an upright frequency spectrum bar on the
left. Dark (anthracite) background. One figure, ready to screenshot/export.
Reused by the Measurements tab (and later Live).
"""

from __future__ import annotations

import numpy as np

from .formats import Measurement

BG = "#232629"       # anthracite, a touch darker than the window
FG = "#e6e6e6"       # light text
ACCENT = "#ff8c2b"   # band / bar
MAXFREQ_KHZ = 65.0


def _style_axes(ax) -> None:
    ax.set_facecolor(BG)
    for spine in ax.spines.values():
        spine.set_color("#4a4f54")
    ax.tick_params(colors=FG, labelsize=8)
    ax.xaxis.label.set_color(FG)
    ax.yaxis.label.set_color(FG)


def analysis_figure(m: Measurement, fig, alpha: float = 1.0,
                    markers: bool = True) -> None:
    """Draw the combined view onto an existing matplotlib Figure (cleared)."""
    fig.clear()
    fig.patch.set_facecolor(BG)
    gs = fig.add_gridspec(1, 2, width_ratios=[1, 8], wspace=0.04)
    ax_bar = fig.add_subplot(gs[0])
    ax_img = fig.add_subplot(gs[1])

    # --- image + overlay ---
    try:
        img = m.composite(alpha=alpha).convert("RGB")
        ax_img.imshow(np.asarray(img))
        w, h = img.size
    except FileNotFoundError:
        ax_img.text(0.5, 0.5, "kein Foto", ha="center", va="center", color=FG)
        w = h = 1
    ax_img.axis("off")

    # one label at the loudest spot: dB, and the frequency directly below it in
    # the same font; offset to the side; no box.
    peaks = m.top_peaks(1)
    if markers and peaks:
        p = peaks[0]
        px, py = p.nx * w, p.ny * h
        label = f"{p.db:.1f} dB"
        if m.dominant_khz:
            label += f"\n{m.dominant_khz:.1f} kHz"
        ax_img.annotate(
            label, (px, py), color=FG, fontsize=13, weight="bold",
            ha="left", va="center", xytext=(18, 0), textcoords="offset points",
            linespacing=1.6,
        )

    # only the measurement name (band + dB are already shown in the image)
    ax_img.set_title(m.name, fontsize=9, color="#9aa0a6")

    # --- upright frequency spectrum bar on the left ---
    _style_axes(ax_bar)
    ax_bar.set_ylim(0, MAXFREQ_KHZ)
    ax_bar.set_xlim(0, 1)
    ax_bar.set_xticks([])
    ticks = [0, 10, 20, 30, 40, 50, 60, 65]
    ax_bar.set_yticks(ticks)
    ax_bar.set_yticklabels([f"{t}K" for t in ticks], fontsize=7)
    auto = bool(m.info.get("autoFreqEnable"))
    if m.spectrum is not None and m.spectrum_hz is not None and m.spectrum.size:
        khz = m.spectrum_hz / 1000.0
        mag = m.spectrum.astype(float).copy()
        mag[(khz > 64.0) | (khz < 0.5)] = 0.0  # drop Nyquist/DC edge artefacts
        # dB (log) scale: noise floor visible but short, real peak stands out.
        pos = mag[mag > 0]
        floor = pos.min() if pos.size else 1e-9
        mdb = 20.0 * np.log10(np.maximum(mag, floor))
        base_db = np.percentile(mdb, 35)        # baseline -> short noise bars
        peak_db = mdb.max()                     # true peak -> full-length bar
        mag = np.clip((mdb - base_db) / (peak_db - base_db + 1e-9), 0.0, 1.0)
        # many thin bars (equaliser style), like the device FFT
        nb = 256
        edges = np.linspace(0, MAXFREQ_KHZ, nb + 1)
        centers = (edges[:-1] + edges[1:]) / 2
        vals = np.zeros(nb)
        idx = np.clip(np.digitize(khz, edges) - 1, 0, nb - 1)
        np.maximum.at(vals, idx, mag)
        colors = [
            ACCENT if (not auto and m.freq_low_khz <= c <= m.freq_high_khz)
            else "#6fa8d6"
            for c in centers
        ]
        ax_bar.barh(centers, vals, height=(MAXFREQ_KHZ / nb) * 0.9,
                    color=colors, linewidth=0)
    # band window: white handle lines at the edges with kHz labels (like device)
    if not auto and m.freq_high_khz > m.freq_low_khz:
        for edge, va in ((m.freq_high_khz, "bottom"), (m.freq_low_khz, "top")):
            ax_bar.axhline(edge, color="white", lw=1.3)
            ax_bar.text(0.96, edge, f"{edge:.1f}", color="white", fontsize=7,
                        ha="right", va=va)
