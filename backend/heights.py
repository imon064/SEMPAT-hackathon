"""Tinggi gedung dari sumber selain tag OSM.

Urutan kepercayaan (yang lebih tinggi tidak ditimpa yang lebih rendah):
  survei petugas > OSM building:levels > OSM height > unggahan perencana > Google 2.5D > tidak ada data.

Sumber yang didukung:
1. GeoJSON unggahan perencana: poligon/titik dengan properti `height` (meter) atau `levels`.
   Bisa berasal dari ekspor Google Earth Engine (tools/ee_export_heights.js), data BPBD, Overture, dsb.
2. Google Open Buildings 2.5D Temporal lewat Earth Engine (opsional, butuh akun & proyek Google Cloud):
   raster `building_height` (m, relatif terhadap tanah) beresolusi efektif ± 4 m untuk 2016-2023.
   Lisensi data: CC BY 4.0 — cantumkan "Google Open Buildings".
"""
from __future__ import annotations

import math
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .area_builder import levels_from_height
from .geo import LocalProjection, point_in_polygon, polygon_area_m2, polygon_centroid

TEMPORAL_ASSET = "GOOGLE/Research/open-buildings-temporal/v1"
V3_ASSET = "GOOGLE/Research/open-buildings/v3/polygons"
PROTECTED_SOURCES = ("survey", "osm", "osm_height")  # tidak ditimpa data perkiraan
SOURCE_RANK = {"default": 0, "google": 1, "upload": 2, "osm_height": 3, "osm": 4, "survey": 5}
Log = Callable[[str], None]


# ---------------------------------------------------------------------------
# GeoJSON
# ---------------------------------------------------------------------------
def _num(v) -> Optional[float]:
    try:
        x = float(str(v).replace(",", ".").replace("m", "").strip())
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def parse_height_features(doc: dict) -> List[dict]:
    """Ambil fitur tinggi gedung dari GeoJSON. Hasil: {ring|point, height, levels, name, bid}."""
    feats = doc.get("features") if doc.get("type") == "FeatureCollection" else [doc]
    out = []
    for f in feats or []:
        if not isinstance(f, dict):
            continue
        props = f.get("properties") or {}
        h = None
        for k in ("height", "building_height", "mean", "tinggi", "height_m"):
            if props.get(k) is not None:
                h = _num(props[k])
                break
        lv = None
        for k in ("levels", "building:levels", "lantai", "jumlah_lantai"):
            if props.get(k) is not None:
                lv = _num(props[k])
                break
        if (h is None or h <= 0) and (lv is None or lv < 1):
            continue
        g = f.get("geometry") or {}
        item = {"height": h if h and h > 0 else None, "levels": int(round(lv)) if lv and lv >= 1 else None,
                "name": str(props.get("name") or props.get("nama") or "")[:80], "bid": props.get("bid")}
        if g.get("type") == "Polygon" and g.get("coordinates"):
            item["ring"] = [(float(x), float(y)) for x, y, *_ in g["coordinates"][0]]
        elif g.get("type") == "MultiPolygon" and g.get("coordinates"):
            item["ring"] = [(float(x), float(y)) for x, y, *_ in g["coordinates"][0][0]]
        elif g.get("type") == "Point" and g.get("coordinates"):
            x, y = g["coordinates"][:2]
            item["point"] = (float(x), float(y))
        elif not item["bid"]:
            continue
        out.append(item)
    return out


def parse_hazard_polygons(doc: dict) -> List[List[Tuple[float, float]]]:
    """Ring luar setiap (Multi)Polygon pada GeoJSON peta bahaya, sebagai [(lon, lat), ...]."""
    feats = doc.get("features") if doc.get("type") == "FeatureCollection" else [doc]
    rings = []
    for f in feats or []:
        g = (f or {}).get("geometry") or (f if (f or {}).get("type") in ("Polygon", "MultiPolygon") else {})
        if g.get("type") == "Polygon":
            polys = [g["coordinates"]]
        elif g.get("type") == "MultiPolygon":
            polys = g["coordinates"]
        else:
            continue
        for poly in polys:
            if poly and len(poly[0]) >= 4:
                rings.append([(float(x), float(y)) for x, y, *_ in poly[0]])
    return rings


# ---------------------------------------------------------------------------
# Penggabungan ke data area
# ---------------------------------------------------------------------------
class _BGrid:
    def __init__(self, buildings: List[dict], proj: LocalProjection, cell: float = 60.0):
        self.cell, self.proj, self.b = cell, proj, buildings
        self.cells: Dict[Tuple[int, int], List[int]] = {}
        self.xy = []
        self.poly = []
        for i, b in enumerate(buildings):
            x, y = proj.to_xy(b["lat"], b["lon"])
            self.xy.append((x, y))
            self.poly.append([proj.to_xy(la, lo) for lo, la in b.get("poly") or []])
            self.cells.setdefault((int(x // cell), int(y // cell)), []).append(i)

    def near(self, x: float, y: float) -> Iterable[int]:
        ci, cj = int(x // self.cell), int(y // self.cell)
        for i in (ci - 1, ci, ci + 1):
            for j in (cj - 1, cj, cj + 1):
                yield from self.cells.get((i, j), ())


def apply_heights(area: dict, engine, features: List[dict], *, source: str = "upload", add_missing: bool = True,
                  min_add_height_m: float = 6.0, log: Log = print) -> dict:
    """Gabungkan tinggi gedung ke area. Mengembalikan statistik. Area diubah langsung (lalu simpan ulang)."""
    proj = LocalProjection(area["meta"]["center"][1], area["meta"]["center"][0])
    blds = area["buildings"]
    grid = _BGrid(blds, proj)
    by_id = {b["id"]: b for b in blds}
    stats = {"features": len(features), "updated": 0, "kept": 0, "added": 0, "unmatched": 0}
    added: List[dict] = []
    for f in features:
        target = by_id.get(f.get("bid")) if f.get("bid") else None
        ring_xy = [proj.to_xy(la, lo) for lo, la in f["ring"]] if f.get("ring") else None
        if ring_xy and len(ring_xy) >= 3:
            fx, fy = polygon_centroid(ring_xy[:-1] if ring_xy[0] == ring_xy[-1] else ring_xy)
        elif f.get("point"):
            fx, fy = proj.to_xy(f["point"][1], f["point"][0])
        elif target is None:
            stats["unmatched"] += 1
            continue
        if target is None:
            best = None
            for i in grid.near(fx, fy):
                bx, by = grid.xy[i]
                d = math.hypot(bx - fx, by - fy)
                inside = (grid.poly[i] and point_in_polygon(fx, fy, grid.poly[i])) or \
                         (ring_xy and point_in_polygon(bx, by, ring_xy))
                if inside or (not ring_xy and d <= 15):
                    if best is None or d < best[0]:
                        best = (d, i)
            target = blds[best[1]] if best else None
        h, lv = f.get("height"), f.get("levels")
        levels = lv if lv else (levels_from_height(h) if h else None)
        if levels is None:
            continue
        if target is not None:
            if SOURCE_RANK.get(target.get("levels_src", "default"), 0) > SOURCE_RANK.get(source, 1):
                stats["kept"] += 1
                continue
            target["levels"] = int(levels)
            target["height_m"] = round(h, 1) if h else None
            target["levels_src"] = source
            if f.get("name") and not target.get("name"):
                target["name"] = f["name"]
            _refresh_candidate(target, area["params"])
            stats["updated"] += 1
            continue
        if not (add_missing and ring_xy and (h or levels * 3.0) >= min_add_height_m):
            stats["unmatched"] += 1
            continue
        nb = _new_building(area, engine, proj, f, ring_xy, levels, h, source, len(blds) + len(added))
        if nb:
            added.append(nb)
            stats["added"] += 1
        else:
            stats["unmatched"] += 1
    blds.extend(added)
    area["meta"].setdefault("counts", {})["buildings"] = len(blds)
    log(f"  Tinggi gedung ({source}): {stats}")
    return stats


def _refresh_candidate(b: dict, params: dict) -> None:
    from .tsunami import WEAK_STRUCTURES
    b["candidate"] = b["levels"] >= params.get("shelter_min_levels", 2) and b["kind"] != "rumah" \
        and b.get("structure") not in WEAK_STRUCTURES
    b["capacity"] = int(b["area_m2"] * (b["levels"] - 1) * params.get("shelter_usable_factor", 0.5)
                        / params.get("space_per_person_m2", 0.93)) if b["candidate"] else 0
    b["climb_s"] = round(max(0, b["levels"] - 1) * 3.0 / 0.21, 1) if b["candidate"] else 0.0


def _new_building(area: dict, engine, proj: LocalProjection, f: dict, ring_xy, levels: int, h: Optional[float],
                  source: str, n: int) -> Optional[dict]:
    ring = ring_xy[:-1] if ring_xy[0] == ring_xy[-1] else ring_xy
    if len(ring) < 3:
        return None
    area_m2 = polygon_area_m2(ring)
    if area_m2 < 40:
        return None
    cx, cy = polygon_centroid(ring)
    lat, lon = proj.to_latlon(cx, cy)
    s = engine.snap(lat, lon)
    if s is None or s["perp_m"] > 300:
        return None
    e, t = s["edge"], s["t"]
    u, v = engine.eu[e], engine.ev[e]
    near = u if t < 0.5 else v
    b = {"id": f"g{n}", "name": f.get("name", ""), "kind": "lainnya", "levels": int(levels), "levels_src": source,
         "height_m": round(h, 1) if h else None, "basement": 0, "structure": "tidak_tahu",
         "area_m2": round(area_m2, 1), "lat": round(lat, 7), "lon": round(lon, 7),
         "poly": [[round(lo, 7), round(la, 7)] for la, lo in (proj.to_latlon(x, y) for x, y in ring)],
         "residents": 0.0, "edge": e, "t": round(t, 4), "perp_m": round(s["perp_m"], 1),
         "elev": round(engine.elev[u] + t * (engine.elev[v] - engine.elev[u]), 1),
         "hazard": int(engine.hazard[near]), "added_from": source}
    _refresh_candidate(b, area["params"])
    return b


def add_source_note(area: dict, note: str) -> None:
    src = [x for x in area["meta"].get("sources", []) if not x.startswith(note.split(":")[0] + ":")]
    area["meta"]["sources"] = src + [note]


# ---------------------------------------------------------------------------
# Google Earth Engine (opsional)
# ---------------------------------------------------------------------------
def ee_available() -> Tuple[bool, str]:
    try:
        import ee  # noqa: F401
    except ImportError:
        return False, "Paket earthengine-api belum terpasang (pip install -r requirements-ee.txt)."
    return True, ""


def ee_fetch_heights(area: dict, *, project: str, year: int = 2023, presence_min: float = 0.5,
                     add_missing: bool = True, min_add_height_m: float = 6.0, max_new: int = 3000,
                     chunk: int = 400, log: Log = print) -> List[dict]:
    """Ambil rata-rata tinggi gedung Google Open Buildings 2.5D untuk setiap bangunan area.

    Bangunan yang sudah punya data lantai dari OSM/survei tidak diminta. Bila add_missing, poligon
    Open Buildings V3 (kepercayaan >= 0,75) yang cukup tinggi ikut diambil untuk gedung yang belum dipetakan.
    """
    import ee
    ee.Initialize(project=project)
    w, s, e, n = area["meta"]["bbox"]
    region = ee.Geometry.Rectangle([w, s, e, n])
    img = (ee.ImageCollection(TEMPORAL_ASSET).filterBounds(region)
           .filterDate(f"{year}-01-01", f"{year + 1}-01-01").mosaic())
    height = img.select("building_height").updateMask(img.select("building_presence").gt(presence_min))
    out: List[dict] = []
    targets = [b for b in area["buildings"]
               if b.get("levels_src", "default") not in PROTECTED_SOURCES and len(b.get("poly") or []) >= 3]
    log(f"  Earth Engine: tinggi untuk {len(targets)} bangunan (tahun {year}) ...")
    for k in range(0, len(targets), chunk):
        part = targets[k:k + chunk]
        fc = ee.FeatureCollection([ee.Feature(ee.Geometry.Polygon([b["poly"] + [b["poly"][0]]]), {"bid": b["id"]})
                                   for b in part])
        res = height.reduceRegions(collection=fc, reducer=ee.Reducer.mean(), scale=4).getInfo()
        for f in res.get("features", []):
            p = f.get("properties") or {}
            h = _num(p.get("mean"))
            if h is not None and h > 0:
                out.append({"bid": p.get("bid"), "height": h, "levels": None, "name": ""})
    if add_missing:
        v3 = (ee.FeatureCollection(V3_ASSET).filterBounds(region)
              .filter(ee.Filter.gte("confidence", 0.75)).filter(ee.Filter.gte("area_in_meters", 40)))
        res = (height.reduceRegions(collection=v3, reducer=ee.Reducer.mean(), scale=4)
               .filter(ee.Filter.gte("mean", min_add_height_m)).limit(max_new).getInfo())
        for f in res.get("features", []):
            g = f.get("geometry") or {}
            h = _num((f.get("properties") or {}).get("mean"))
            if g.get("type") == "Polygon" and h:
                out.append({"ring": [(float(x), float(y)) for x, y, *_ in g["coordinates"][0]], "height": h,
                            "levels": None, "name": ""})
    log(f"  Earth Engine: {len(out)} nilai tinggi diterima.")
    return out
