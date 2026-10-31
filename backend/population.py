"""Penduduk dari grid kepadatan penduduk (mis. Meta/CIESIN High Resolution Population Density Maps, ±30 m).

Sumber yang dirancang (BELUM diuji dengan berkas nyata di lingkungan pengembangan; uji memakai grid SINTETIS):
- Meta & CIESIN "High Resolution Population Density Maps + Demographic Estimates" (HRSL): 1 arc-second (±30 m),
  CSV & GeoTIFF, lisensi CC BY 4.0. HDX (per negara) dan AWS Open Data (s3://dataforgood-fb-data/csv/).
  Kolom CSV bervariasi antar-versi (mis. "longitude,latitude,population_2020" atau "Lat,Lon,Population"), jadi
  kolom dikenali dari namanya.
- GeoTIFF tidak dibaca langsung (perlu pustaka raster); ubah dulu ke CSV (mis. `gdal2xyz.py -csv`).

Distribusi ke data area (deterministik, `apply_grids`):
1. Setiap sel = kotak `cell_deg` di sekitar titik pusatnya. Penduduk sel dibagi ke bangunan HUNIAN (aturan
   penduduk lama: `residents_model > 0`) yang titik beratnya di dalam sel, sebanding luas tapak.
2. Tanpa bangunan hunian di sel: ke bangunan hunian terdekat <= `NEAREST_M` dari pusat sel.
3. Selain itu (mis. peta otomatis yang tidak mengunduh rumah): ASAL VIRTUAL di pusat sel, ditempelkan ke jalan
   terdekat <= `SNAP_M` (tanpa poligon; hanya untuk alokasi, tidak tampil sebagai bangunan).
4. Sel yang tidak bisa ditempelkan dihitung sebagai "tidak teralokasi" dan dilaporkan.
Bangunan hunian tanpa sel mendapat 0 orang (grid berlaku untuk seluruh area yang dicakupnya).
Tanpa grid: model lama (4 orang/rumah) tetap dipakai dan SELALU diberi label "perkiraan model".
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .geo import LocalProjection, SegmentGrid, haversine_m

CELL_DEG_DEFAULT = 1.0 / 3600.0  # 1 arc-second
NEAREST_M = 45.0
SNAP_M = 300.0
MAX_CELLS = 2_000_000
MODEL_LABEL = "PERKIRAAN MODEL: orang per rumah (bukan data kependudukan)"
_LAT = re.compile(r"^(lat|latitude|y)$", re.I)
_LON = re.compile(r"^(lon|long|longitude|lng|x)$", re.I)
_POP = re.compile(r"^(pop|population|population_\d{4}|.*_\d{4}|value|count|z)$", re.I)


def parse_csv(text: str, max_cells: int = MAX_CELLS) -> List[Tuple[float, float, float]]:
    """CSV -> [(lon, lat, penduduk)]. Header wajib; kolom lat/lon dikenali dari nama; penduduk = kolom lain pertama
    yang cocok `_POP` (atau satu-satunya kolom numerik lain). Baris rusak dilewati; nilai <= 0 dibuang."""
    rd = csv.reader(io.StringIO(text))
    head = next(rd, None)
    if not head:
        raise ValueError("Berkas kosong.")
    head = [h.strip().strip('"') for h in head]
    try:
        ila = next(i for i, h in enumerate(head) if _LAT.match(h))
        ilo = next(i for i, h in enumerate(head) if _LON.match(h))
    except StopIteration:
        raise ValueError("Kolom lintang/bujur tidak dikenali (contoh header: longitude,latitude,population_2020).")
    rest = [i for i in range(len(head)) if i not in (ila, ilo)]
    ipo = next((i for i in rest if _POP.match(head[i])), rest[0] if len(rest) == 1 else None)
    if ipo is None:
        raise ValueError("Kolom jumlah penduduk tidak dikenali.")
    out = []
    for row in rd:
        try:
            la, lo, po = float(row[ila]), float(row[ilo]), float(row[ipo])
        except (ValueError, IndexError):
            continue
        if not (math.isfinite(la) and math.isfinite(lo) and math.isfinite(po)) or po <= 0:
            continue
        if not (-90 <= la <= 90 and -180 <= lo <= 180):
            continue
        out.append((lo, la, po))
        if len(out) > max_cells:
            raise ValueError(f"Terlalu banyak sel (> {max_cells}); potong dulu ke wilayah kerja.")
    if not out:
        raise ValueError("Tidak ada sel penduduk yang valid.")
    return out


def make_grid(cells: Sequence[Tuple[float, float, float]], *, gid: str, name: str, source: str, license: str,
              cell_deg: float = CELL_DEG_DEFAULT, synthetic: bool = False, year: Optional[str] = None) -> dict:
    lons = [c[0] for c in cells]
    lats = [c[1] for c in cells]
    return {"id": gid, "name": name, "source": source, "license": license, "cell_deg": cell_deg, "year": year,
            "synthetic": bool(synthetic), "created": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "bbox": [min(lons), min(lats), max(lons), max(lats)], "count": len(cells),
            "total": round(sum(c[2] for c in cells), 1),
            "lon": [round(x, 6) for x in lons], "lat": [round(x, 6) for x in lats],
            "pop": [round(c[2], 3) for c in cells]}


# ---------------------------------------------------------------------------
# Penyimpanan grid regional (data/population/<id>.json): sekali unggah, dipakai semua area yang tercakup
# ---------------------------------------------------------------------------
class GridStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._cache: Dict[str, dict] = {}

    def save(self, grid: dict) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{grid['id']}.json").write_text(json.dumps(grid, separators=(",", ":")), encoding="utf-8")
        self._cache[grid["id"]] = grid
        return summary(grid)

    def delete(self, gid: str) -> None:
        p = self.root / f"{gid}.json"
        if p.exists():
            p.unlink()
        self._cache.pop(gid, None)

    def all(self) -> List[dict]:
        out = []
        if not self.root.exists():
            return out
        for p in sorted(self.root.glob("*.json")):
            gid = p.stem
            if gid not in self._cache:
                try:
                    self._cache[gid] = json.loads(p.read_text(encoding="utf-8"))
                except Exception as exc:  # berkas rusak tidak menghentikan server
                    print(f"Grid penduduk {gid} dilewati: {exc}")
                    continue
            out.append(self._cache[gid])
        return out

    def covering(self, bbox: Sequence[float]) -> List[dict]:
        w, s, e, n = bbox
        return [g for g in self.all() if not (g["bbox"][2] < w or g["bbox"][0] > e or g["bbox"][3] < s or g["bbox"][1] > n)]


def summary(g: dict) -> dict:
    return {k: g.get(k) for k in ("id", "name", "source", "license", "cell_deg", "year", "synthetic", "created", "bbox",
                                  "count", "total")}


# ---------------------------------------------------------------------------
# Penerapan ke area
# ---------------------------------------------------------------------------
def _is_dwelling(b: dict) -> bool:
    return float(b.get("residents_model", b.get("residents") or 0) or 0) > 0 and not b.get("virtual")


def reset_to_model(area: dict) -> None:
    """Kembalikan penduduk ke model lama (orang per rumah) dan buang asal virtual."""
    area["buildings"] = [b for b in area["buildings"] if not b.get("virtual")]
    for b in area["buildings"]:
        if "residents_model" in b:
            b["residents"] = b["residents_model"]
        b["residents_src"] = "model" if float(b.get("residents") or 0) > 0 else None
    p = area.get("params", {})
    area["meta"]["population"] = {"method": "model", "label": MODEL_LABEL,
                                  "persons_per_house": p.get("persons_per_house"),
                                  "total": round(sum(float(b.get("residents") or 0) for b in area["buildings"]), 1)}


def apply_grids(area: dict, grids: Sequence[dict], snap) -> dict:
    """Terapkan grid penduduk yang mencakup area. `snap(lat, lon)` = Engine.snap (asal virtual ke jalan).

    Mengubah area langsung: residents per bangunan (+ residents_src = "grid"), asal virtual, meta.population.
    Tanpa grid yang tumpang tindih: model lama berlabel jelas (reset_to_model).
    """
    for b in area["buildings"]:
        if not b.get("virtual") and "residents_model" not in b:
            b["residents_model"] = float(b.get("residents") or 0)
    reset_to_model(area)
    w, s, e, n = extent(area)
    cells = []
    used = []
    for g in grids:
        cnt = 0
        for lo, la, po in zip(g["lon"], g["lat"], g["pop"]):
            if w <= lo <= e and s <= la <= n:
                cells.append((lo, la, po, g["cell_deg"]))
                cnt += 1
        if cnt:
            used.append(dict(summary(g), cells_in_area=cnt))
    if not cells:
        return area["meta"]["population"]
    proj = LocalProjection(area["meta"]["center"][1], area["meta"]["center"][0])
    dw = [b for b in area["buildings"] if _is_dwelling(b)]
    cell_m = 60.0
    idx: Dict[Tuple[int, int], List[dict]] = {}
    for b in dw:
        x, y = proj.to_xy(b["lat"], b["lon"])
        b["_xy"] = (x, y)
        idx.setdefault((int(x // cell_m), int(y // cell_m)), []).append(b)
        b["residents"] = 0.0
        b["residents_src"] = "grid"
    virtual: List[dict] = []
    st = {"cells": len(cells), "to_buildings": 0.0, "to_nearest": 0.0, "to_virtual": 0.0, "unassigned": 0.0,
          "virtual_origins": 0}
    for lo, la, po, cd in cells:
        half = cd / 2.0
        inside = [b for b in _near(idx, proj.to_xy(la, lo), cell_m)
                  if abs(b["lat"] - la) <= half and abs(b["lon"] - lo) <= half]
        if inside:
            wsum = sum(max(1.0, float(b.get("area_m2") or 1.0)) for b in inside)
            for b in inside:
                b["residents"] += po * max(1.0, float(b.get("area_m2") or 1.0)) / wsum
            st["to_buildings"] += po
            continue
        cx, cy = proj.to_xy(la, lo)
        best = None
        for b in _near(idx, (cx, cy), cell_m):
            d = math.hypot(b["_xy"][0] - cx, b["_xy"][1] - cy)
            if d <= NEAREST_M and (best is None or d < best[0]):
                best = (d, b)
        if best:
            best[1]["residents"] += po
            st["to_nearest"] += po
            continue
        sn = snap(la, lo)
        if sn is None or sn["perp_m"] > SNAP_M:
            st["unassigned"] += po
            continue
        virtual.append({"id": f"pop:{lo:.5f},{la:.5f}", "name": "", "kind": "sel_penduduk", "virtual": True,
                        "levels": 1, "levels_src": "default", "structure": "tidak_tahu", "area_m2": 0.0,
                        "lat": round(la, 7), "lon": round(lo, 7), "residents": round(po, 3),
                        "residents_model": 0.0, "residents_src": "grid", "edge": sn["edge"], "t": round(sn["t"], 4),
                        "perp_m": round(sn["perp_m"], 1), "elev": None, "hazard": 0, "osm_unsafe": "",
                        "tier": "unsafe", "tier_reasons": ["Sel penduduk (bukan bangunan)"], "survey_prio": False})
        st["to_virtual"] += po
    for b in dw:
        b.pop("_xy", None)
        b["residents"] = round(b["residents"], 3)
    area["buildings"].extend(virtual)
    st["virtual_origins"] = len(virtual)
    total = st["to_buildings"] + st["to_nearest"] + st["to_virtual"]
    area["meta"]["population"] = {
        "method": "grid", "grids": used, "synthetic": any(g.get("synthetic") for g in used),
        "label": ("GRID SINTETIS (uji) — bukan data penduduk nyata" if any(g.get("synthetic") for g in used)
                  else "Grid penduduk: " + "; ".join(f"{g['name']} ({g['license']})" for g in used)),
        "total": round(total, 1), **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in st.items()}}
    return area["meta"]["population"]


def extent(area: dict) -> Tuple[float, float, float, float]:
    """Cakupan area untuk grid penduduk: bbox area DIGABUNG sebaran bangunan (bangunan bisa di luar sebaran simpul
    jalan; tanpa ini penduduknya hilang saat grid diterapkan) + setengah sel."""
    w, s, e, n = area["meta"]["bbox"]
    for b in area.get("buildings", []):
        if not b.get("virtual"):
            w, e = min(w, b["lon"]), max(e, b["lon"])
            s, n = min(s, b["lat"]), max(n, b["lat"])
    pad = CELL_DEG_DEFAULT
    return w - pad, s - pad, e + pad, n + pad


def _near(idx, xy, cell_m):
    ci, cj = int(xy[0] // cell_m), int(xy[1] // cell_m)
    for i in (ci - 1, ci, ci + 1):
        for j in (cj - 1, cj, cj + 1):
            yield from idx.get((i, j), ())


def edge_snapper(area: dict):
    """snap(lat, lon) -> {edge, t, perp_m} ke ruas jalan terdekat (sama dengan Engine.snap, tanpa membangun graf)."""
    N, E = area["nodes"], area["edges"]
    proj = LocalProjection(area["meta"]["center"][1], area["meta"]["center"][0])
    xy = [proj.to_xy(a, b) for a, b in zip(N["lat"], N["lon"])]
    grid = SegmentGrid(cell_m=80.0)
    for e, (u, v) in enumerate(zip(E["u"], E["v"])):
        grid.add(xy[u][0], xy[u][1], xy[v][0], xy[v][1], e)

    def snap(lat, lon):
        x, y = proj.to_xy(lat, lon)
        hit = grid.nearest(x, y, max_radius_m=SNAP_M + 50)
        if hit is None:
            return None
        d, t, _, _, e = hit
        return {"edge": e, "t": t, "perp_m": d}
    return snap


def density_cells(area: dict, grids: Sequence[dict], limit: int = 60000) -> dict:
    """Sel grid di dalam area untuk visualisasi kepadatan (perencana). HANYA dari grid yang diunggah; model 4/rumah
    TIDAK pernah dijadikan peta kepadatan."""
    w, s, e, n = extent(area)
    out = {"lon": [], "lat": [], "pop": [], "cell_deg": None, "label": None, "synthetic": False}
    for g in grids:
        for lo, la, po in zip(g["lon"], g["lat"], g["pop"]):
            if w <= lo <= e and s <= la <= n:
                out["lon"].append(lo)
                out["lat"].append(la)
                out["pop"].append(po)
        out["cell_deg"] = g["cell_deg"]
        out["synthetic"] = out["synthetic"] or bool(g.get("synthetic"))
        out["label"] = ("GRID SINTETIS (uji) — bukan data penduduk nyata" if out["synthetic"]
                        else f"{g['name']} ({g['license']})")
    if len(out["pop"]) > limit:
        order = sorted(range(len(out["pop"])), key=lambda i: -out["pop"][i])[:limit]
        out = dict(out, lon=[out["lon"][i] for i in order], lat=[out["lat"][i] for i in order],
                   pop=[out["pop"][i] for i in order], truncated=True)
    out["count"] = len(out["pop"])
    return out


def synthetic_grid_for(area: dict, people_per_cell: float = 6.0, gid: str = "sintetis-uji") -> dict:
    """Grid SINTETIS untuk uji (berlabel synthetic=True). Satu sel per bangunan hunian model + sel acak di jalan."""
    cells = []
    for b in area["buildings"]:
        if float(b.get("residents") or 0) > 0 and not b.get("virtual"):
            lo = round(b["lon"] * 3600) / 3600
            la = round(b["lat"] * 3600) / 3600
            cells.append((lo, la, people_per_cell))
    seen = {}
    for lo, la, po in cells:
        seen[(lo, la)] = seen.get((lo, la), 0.0) + po
    return make_grid([(lo, la, po) for (lo, la), po in seen.items()], gid=gid, name="Grid uji SINTETIS",
                     source="sintetis (tests)", license="-", synthetic=True)


def haversine(a, b):  # pragma: no cover - utilitas kecil untuk alat
    return haversine_m(a[1], a[0], b[1], b[0])
