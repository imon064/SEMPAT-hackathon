"""Model kecepatan berjalan saat evakuasi.

Sumber parameter:
- Kecepatan dasar 1,0 m/s (warga umum) dan 0,5 m/s (penyandang disabilitas,
  warga yang sulit berjalan, bayi) serta 0,21 m/s untuk naik tangga mengikuti
  pedoman perencanaan evakuasi tsunami Jepang (MLIT).
- Pengaruh kemiringan memakai fungsi Tobler:
  W(s) = 6 * exp(-3.5 * |s + 0.05|) km/jam, dinormalkan terhadap W(0).
"""
from __future__ import annotations

import math

GROUP_SPEED_MPS = {"umum": 1.0, "rentan": 0.5}
GROUPS = tuple(GROUP_SPEED_MPS.keys())
STAIR_VERTICAL_MPS = 0.21
FLOOR_HEIGHT_M = 3.0
MAX_ABS_SLOPE = 1.0  # batas kemiringan agar data elevasi yang kasar tidak membuat nilai ekstrem

_W0 = 6.0 * math.exp(-3.5 * 0.05)


def tobler_factor(slope: float) -> float:
    """Faktor pengali kecepatan terhadap jalan datar (1,0 di s = 0)."""
    s = max(-MAX_ABS_SLOPE, min(MAX_ABS_SLOPE, slope))
    return (6.0 * math.exp(-3.5 * abs(s + 0.05))) / _W0


def edge_time_s(length_m: float, dz_m: float, group: str, kind: str = "road") -> float:
    """Waktu tempuh satu arah sebuah ruas (detik).

    dz_m = elevasi ujung tujuan dikurangi elevasi ujung asal.
    """
    if length_m <= 0:
        return 0.0
    base = GROUP_SPEED_MPS[group]
    slope = dz_m / length_m
    speed = base * tobler_factor(slope)
    t = length_m / speed
    if kind == "steps":
        # Tangga: dibatasi kecepatan vertikal naik tangga bila menanjak.
        t = max(t, max(0.0, dz_m) / STAIR_VERTICAL_MPS, length_m / (base * 0.5))
    return t


def climb_time_s(levels: int) -> float:
    """Waktu naik dari lantai dasar ke lantai teratas gedung (detik)."""
    return max(0, levels - 1) * FLOOR_HEIGHT_M / STAIR_VERTICAL_MPS


def flat_time_s(length_m: float, group: str) -> float:
    return length_m / GROUP_SPEED_MPS[group]
