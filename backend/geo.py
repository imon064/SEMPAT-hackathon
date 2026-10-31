"""Utilitas geometri untuk skala desa.

Semua jarak dihitung dalam meter. Untuk area desa (beberapa kilometer),
proyeksi equirectangular lokal memberi galat jauh di bawah 0,1%,
sehingga cukup akurat dan jauh lebih cepat daripada haversine berulang.
"""
from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Tuple

EARTH_RADIUS_M = 6371008.8


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Jarak lingkaran besar antara dua titik (meter)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Arah (0 = utara, 90 = timur) dari titik 1 ke titik 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


class LocalProjection:
    """Proyeksi lokal lat/lon -> meter (x ke timur, y ke utara)."""

    def __init__(self, lat0: float, lon0: float):
        self.lat0 = lat0
        self.lon0 = lon0
        self.kx = math.radians(1.0) * EARTH_RADIUS_M * math.cos(math.radians(lat0))
        self.ky = math.radians(1.0) * EARTH_RADIUS_M

    def to_xy(self, lat: float, lon: float) -> Tuple[float, float]:
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky

    def to_latlon(self, x: float, y: float) -> Tuple[float, float]:
        return self.lat0 + y / self.ky, self.lon0 + x / self.kx


def project_point_on_segment(px: float, py: float, ax: float, ay: float,
                             bx: float, by: float) -> Tuple[float, float, float, float]:
    """Proyeksi titik P ke segmen AB.

    Mengembalikan (t, jarak, qx, qy) dengan t di [0, 1] posisi sepanjang AB.
    """
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 <= 1e-12:
        t = 0.0
    else:
        t = ((px - ax) * dx + (py - ay) * dy) / seg2
        t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    qx, qy = ax + t * dx, ay + t * dy
    return t, math.hypot(px - qx, py - qy), qx, qy


def polygon_area_m2(xy: Sequence[Tuple[float, float]]) -> float:
    """Luas poligon (rumus shoelace) dalam m²."""
    n = len(xy)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = xy[i]
        x2, y2 = xy[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def polygon_centroid(xy: Sequence[Tuple[float, float]]) -> Tuple[float, float]:
    """Titik berat poligon; jatuh ke rata-rata titik bila luasnya nol."""
    n = len(xy)
    a = 0.0
    cx = cy = 0.0
    for i in range(n):
        x1, y1 = xy[i]
        x2, y2 = xy[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        a += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(a) < 1e-9:
        return sum(p[0] for p in xy) / n, sum(p[1] for p in xy) / n
    a *= 0.5
    return cx / (6 * a), cy / (6 * a)


def point_in_polygon(x: float, y: float, ring: Sequence[Tuple[float, float]]) -> bool:
    """Uji titik di dalam poligon (ray casting)."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


class SegmentGrid:
    """Indeks spasial grid sederhana untuk mencari segmen terdekat."""

    def __init__(self, cell_m: float = 100.0):
        self.cell = cell_m
        self.cells: dict = {}
        self.segs: List[Tuple[float, float, float, float, object]] = []

    def _key(self, x: float, y: float) -> Tuple[int, int]:
        return int(math.floor(x / self.cell)), int(math.floor(y / self.cell))

    def add(self, ax: float, ay: float, bx: float, by: float, ref: object) -> None:
        idx = len(self.segs)
        self.segs.append((ax, ay, bx, by, ref))
        x0, y0 = self._key(min(ax, bx), min(ay, by))
        x1, y1 = self._key(max(ax, bx), max(ay, by))
        for i in range(x0, x1 + 1):
            for j in range(y0, y1 + 1):
                self.cells.setdefault((i, j), []).append(idx)

    def nearest(self, x: float, y: float, max_radius_m: float = 5000.0
                ) -> Optional[Tuple[float, float, float, float, object]]:
        """Segmen terdekat: (jarak, t, qx, qy, ref) atau None."""
        if not self.segs:
            return None
        ci, cj = self._key(x, y)
        best = None
        ring = 0
        max_ring = int(max_radius_m / self.cell) + 1
        seen = set()
        while ring <= max_ring:
            for i in range(ci - ring, ci + ring + 1):
                for j in range(cj - ring, cj + ring + 1):
                    if ring and abs(i - ci) != ring and abs(j - cj) != ring:
                        continue
                    for idx in self.cells.get((i, j), ()):
                        if idx in seen:
                            continue
                        seen.add(idx)
                        ax, ay, bx, by, ref = self.segs[idx]
                        t, d, qx, qy = project_point_on_segment(x, y, ax, ay, bx, by)
                        if best is None or d < best[0]:
                            best = (d, t, qx, qy, ref)
            # Setelah ada kandidat, cukup satu cincin tambahan untuk menjamin terdekat.
            if best is not None and best[0] <= ring * self.cell:
                break
            ring += 1
        return best


def iter_pairs(seq: Sequence) -> Iterable[Tuple[object, object]]:
    for i in range(len(seq) - 1):
        yield seq[i], seq[i + 1]
