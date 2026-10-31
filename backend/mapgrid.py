"""Grid peta turunan jaringan jalan: zona bahaya (aturan) dan waktu evakuasi, untuk VISUALISASI.

Metode (sama untuk kedua lapisan, deterministik):
- Area dibagi sel persegi `cell_m` (bawaan 30 m sesuai proposal untuk waktu evakuasi; 80 m untuk zona bahaya).
- Nilai sel = nilai SIMPUL JALAN TERDEKAT dari pusat sel, hanya bila simpul itu <= `max_dist_m` (bawaan 2 sel,
  min. 60 m). Sel yang lebih jauh dari jalan DIBIARKAN KOSONG (tidak ditebak).
- Waktu evakuasi sel = waktu tabel simpul itu (Dijkstra, sama dengan rute HP) + jalan lurus pusat sel -> simpul
  pada kecepatan datar kelompok. Dikelompokkan per `band_s` (bawaan 180 s = 3 menit sesuai proposal).
- Keterbatasan: resolusi 30 m BUKAN akurasi 30 m. Akurasi dibatasi data jalan (OSM), DEM (±30–90 m, galat beberapa
  meter), dan model kecepatan; jalan setapak/halaman yang tidak dipetakan tidak terlihat; bangunan & pagar tidak
  dimodelkan sebagai penghalang.
Keluaran ringkas: baris sel berurutan dengan nilai sama digabung menjadi "run" [i_awal, j, panjang, nilai].
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .geo import LocalProjection

MAX_CELLS = 400_000


def _index(xy: Sequence[Tuple[float, float]], cell: float) -> Dict[Tuple[int, int], List[int]]:
    idx: Dict[Tuple[int, int], List[int]] = {}
    for k, (x, y) in enumerate(xy):
        idx.setdefault((int(math.floor(x / cell)), int(math.floor(y / cell))), []).append(k)
    return idx


def node_grid(area: dict, values: Sequence[Optional[float]], *, cell_m: float, max_dist_m: Optional[float] = None,
              transform: Optional[Callable[[float, float], Optional[float]]] = None, pad_m: float = 0.0) -> dict:
    """Grid nilai dari simpul terdekat. values[k] None = simpul diabaikan. transform(nilai, jarak_m) -> nilai sel."""
    N = area["nodes"]
    lat0, lon0 = area["meta"]["center"][1], area["meta"]["center"][0]
    proj = LocalProjection(lat0, lon0)
    xy = [proj.to_xy(a, b) for a, b in zip(N["lat"], N["lon"])]
    keep = [k for k, v in enumerate(values) if v is not None]
    if not keep:
        return {"cell_m": cell_m, "runs": [], "origin": None}
    md = max_dist_m if max_dist_m is not None else max(60.0, 2 * cell_m)
    kx = [xy[k] for k in keep]
    xs, ys = [p[0] for p in kx], [p[1] for p in kx]
    x0, y0 = min(xs) - md - pad_m, min(ys) - md - pad_m
    nx = int(math.ceil((max(xs) + md + pad_m - x0) / cell_m))
    ny = int(math.ceil((max(ys) + md + pad_m - y0) / cell_m))
    if nx * ny > MAX_CELLS:  # area sangat besar: perbesar sel (dicatat di keluaran)
        f = math.sqrt(nx * ny / MAX_CELLS)
        cell_m *= f
        nx, ny = int(math.ceil(nx / f)), int(math.ceil(ny / f))
    bucket = md
    idx = _index(kx, bucket)
    runs = []
    r = int(math.ceil(md / bucket))
    for j in range(ny):
        cy = y0 + (j + 0.5) * cell_m
        cur = None
        for i in range(nx):
            cx = x0 + (i + 0.5) * cell_m
            bi, bj = int(math.floor(cx / bucket)), int(math.floor(cy / bucket))
            best = None
            for a in range(bi - r, bi + r + 1):
                for b in range(bj - r, bj + r + 1):
                    for k in idx.get((a, b), ()):
                        d = math.hypot(kx[k][0] - cx, kx[k][1] - cy)
                        if d <= md and (best is None or d < best[0]):
                            best = (d, k)
            v = None
            if best is not None:
                raw = values[keep[best[1]]]
                v = transform(raw, best[0]) if transform else raw
            if cur is not None and v == cur[3] and cur[0] + cur[2] == i:
                cur[2] += 1
                continue
            if cur is not None and cur[3] is not None:
                runs.append(cur)
            cur = [i, j, 1, v]
        if cur is not None and cur[3] is not None:
            runs.append(cur)
    lat_s, lon_w = proj.to_latlon(x0, y0)
    return {"cell_m": round(cell_m, 2), "origin": [round(lon_w, 7), round(lat_s, 7)],
            "dlat": cell_m / 111320.0, "dlon": cell_m / (111320.0 * math.cos(math.radians(lat0))),
            "nx": nx, "ny": ny, "max_dist_m": md, "runs": runs}


def hazard_view(area: dict, cell_m: float = 80.0) -> dict:
    """Zona bahaya untuk peta: poligon resmi bila ada; selain itu sel dari aturan ASUMSI (simpul `hazard`)."""
    meta = area.get("meta", {})
    if meta.get("hazard_method") == "polygon":
        src = next((x.split(":", 1)[1].strip() for x in meta.get("sources", []) if x.startswith("Zona bahaya:")),
                   "peta bahaya resmi")
        return {"method": "polygon", "official": True, "label": f"Zona bahaya — peta resmi ({src})"}
    p = area.get("params", {})
    vals = [1.0 if h else None for h in area["nodes"]["hazard"]]
    g = node_grid(area, vals, cell_m=cell_m)
    g.update(method="rule", official=False,
             label=(f"Zona bahaya — PERKIRAAN aturan (elevasi < {p.get('hazard_elev_threshold_m', 20):g} m dan "
                    f"< {p.get('hazard_max_coast_dist_m', 3000) / 1000:g} km dari pantai), BUKAN peta resmi"))
    return g


def time_grid(area: dict, table: dict, *, speed_mps: float, cell_m: float = 30.0, band_s: float = 180.0,
              max_bands: int = 10, label: str = "") -> dict:
    """Grid waktu evakuasi (pita `band_s`) dari tabel HP satu mode/kelompok. Nilai = indeks pita (0 = 0–3 menit)."""
    times = table["time"]
    vals = [float(t) if t is not None and t >= 0 else None for t in times]

    def tf(t, d):
        tot = t + d / speed_mps
        return min(max_bands, int(tot // band_s))
    g = node_grid(area, vals, cell_m=cell_m, transform=tf)
    g.update(band_s=band_s, max_bands=max_bands, label=label,
             method=("Waktu tabel simpul jalan terdekat (Dijkstra) + jalan lurus ke simpul itu; sel > "
                     f"{g.get('max_dist_m', 0):g} m dari jalan kosong. Resolusi {cell_m:g} m bukan akurasi {cell_m:g} m."))
    return g
