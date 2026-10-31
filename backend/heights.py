"""Tinggi gedung dari sumber selain tag OSM.

Urutan kepercayaan (yang lebih tinggi tidak ditimpa yang lebih rendah):
  survei petugas > OSM building:levels / building:part > OSM height > unggahan perencana > Google 2.5D > tidak ada data.

Survei petugas TIDAK disimpan di area.json: ia lapisan di atas data otomatis (lihat app.AreaCtx.apply_survey).
Fungsi di sini selalu bekerja pada lapisan otomatis, sehingga data unggahan/Google tetap tersimpan walau gedung sedang
tersurvei, dan muncul kembali bila survei dibatalkan.

Sumber yang didukung:
1. GeoJSON unggahan perencana: poligon/titik dengan properti `height` (meter/feet) atau `levels`.
   Fitur dengan properti `sempat_source = "google_open_buildings"` (hasil tools/ee_export_heights.js) dicatat
   sebagai sumber "google", bukan "upload".
2. Google Open Buildings 2.5D Temporal lewat Earth Engine (opsional, butuh akun & proyek Google Cloud):
   raster `building_height` (m, relatif terhadap tanah) beresolusi efektif ± 4 m untuk 2016-2023.
   Lisensi data: CC BY 4.0 — cantumkan "Google Open Buildings". BELUM divalidasi dengan Earth Engine asli.

Jumlah lantai dari tinggi = floor(tinggi / params["height_per_level_m"]) (keputusan proyek: 4 m per lantai) dan
ditandai levels_est=True. Tinggi tempat ibadah TIDAK dipakai untuk menebak lantai (kubah/menara) -> perlu survei.
Bila sumber berbeda memberi jumlah lantai yang selisihnya >= CONFLICT_MIN_LEVELS, gedung diberi `levels_conflict`
(sumber -> lantai) dan masuk prioritas survei; peringkat sumber tetap menentukan nilai yang dipakai.
"""
from __future__ import annotations

import hashlib
import math
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from . import tsunami
from .area_builder import DEFAULT_PARAMS, building_elevation, levels_from_height, parse_height_m, parse_levels
from .geo import LocalProjection, point_in_polygon, polygon_area_m2, polygon_centroid

TEMPORAL_ASSET = "GOOGLE/Research/open-buildings-temporal/v1"
V3_ASSET = "GOOGLE/Research/open-buildings/v3/polygons"
PROTECTED_SOURCES = ("survey", "osm", "osm_height", "osm_part")  # tidak ditimpa data perkiraan
SOURCE_RANK = {"default": 0, "google": 1, "upload": 2, "osm_height": 3, "osm": 4, "osm_part": 4, "survey": 5}
GOOGLE_MARKERS = ("google_open_buildings", "google")
ID_PREFIX = {"google": "gob-", "upload": "upl-"}  # ID gedung tambahan; "g{n}" lama tetap dikenali
CONFLICT_MIN_LEVELS = 2  # selisih jumlah lantai antar-sumber yang dicatat sebagai konflik (HEURISTIK)
Log = Callable[[str], None]


# ---------------------------------------------------------------------------
# GeoJSON
# ---------------------------------------------------------------------------
def _ring(coords) -> Optional[List[Tuple[float, float]]]:
    """Cincin (lon, lat) yang valid: >= 4 titik, angka terhingga, di dalam rentang koordinat."""
    try:
        r = [(float(c[0]), float(c[1])) for c in coords]
    except (TypeError, ValueError, IndexError):
        return None
    if len(r) < 4 or any(not (math.isfinite(x) and math.isfinite(y) and -180 <= x <= 180 and -90 <= y <= 90)
                         for x, y in r):
        return None
    return r


def _ring_size(r: List[Tuple[float, float]]) -> float:
    k = math.cos(math.radians(sum(y for _, y in r) / len(r)))
    return polygon_area_m2([(x * k, y) for x, y in r])


def parse_height_features(doc: dict) -> List[dict]:
    """Ambil fitur tinggi gedung dari GeoJSON. Hasil: {ring|point, height, levels, name, bid, source, uid}.

    Geometri tidak valid dilewati (fitur tetap dipakai bila punya `bid`). MultiPolygon: poligon terbesar.
    """
    feats = doc.get("features") if doc.get("type") == "FeatureCollection" else [doc]
    out = []
    for f in feats or []:
        if not isinstance(f, dict):
            continue
        props = f.get("properties") or {}
        h = None
        for k in ("height", "building_height", "mean", "tinggi", "height_m"):
            if props.get(k) is not None:
                h = parse_height_m(props[k])
                break
        lv = None
        for k in ("levels", "building:levels", "lantai", "jumlah_lantai"):
            if props.get(k) is not None:
                lv = parse_levels(props[k])
                break
        if h is None and lv is None:
            continue
        src = "google" if str(props.get("sempat_source", "")).strip().lower() in GOOGLE_MARKERS else None
        item = {"height": h, "levels": lv, "name": str(props.get("name") or props.get("nama") or "")[:80],
                "bid": props.get("bid"), "source": src,
                "uid": props.get("full_plus_code") or props.get("plus_code") or None}
        g = f.get("geometry") or {}
        typ, coords = g.get("type"), g.get("coordinates")
        ring = None
        if typ == "Polygon" and coords:
            ring = _ring(coords[0])
        elif typ == "MultiPolygon" and coords:
            rings = [r for r in (_ring(poly[0]) for poly in coords if poly) if r]
            ring = max(rings, key=_ring_size) if rings else None
        elif typ == "Point" and coords:
            try:
                x, y = float(coords[0]), float(coords[1])
                if math.isfinite(x) and math.isfinite(y):
                    item["point"] = (x, y)
            except (TypeError, ValueError, IndexError):
                pass
        if ring:
            item["ring"] = ring
        if not (ring or item.get("point") or item["bid"]):
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


def stable_building_id(source: str, ring_lonlat: List[Tuple[float, float]], uid: Optional[str] = None) -> str:
    """ID deterministik untuk gedung tambahan: tidak bergantung pada urutan fitur maupun pusat proyeksi area.

    Kunci = kode Plus Open Buildings bila ada, selain itu titik berat footprint (lon/lat, 6 desimal ≈ 0,1 m).
    Dua footprint dengan titik berat sama persis dianggap gedung yang sama (bukan duplikat baru).
    """
    if uid:
        key = f"pc:{str(uid).strip().upper()}"
    else:
        r = ring_lonlat[:-1] if len(ring_lonlat) > 1 and ring_lonlat[0] == ring_lonlat[-1] else ring_lonlat
        cx, cy = polygon_centroid(r)
        key = f"{cy:.6f},{cx:.6f}"
    return ID_PREFIX.get(source, "add-") + hashlib.sha1(key.encode()).hexdigest()[:12]


def _set_levels(target: dict, levels: int, h: Optional[float], src: str, est: bool, name: str, params: dict) -> None:
    target["levels"] = int(levels)
    target["height_m"] = round(h, 1) if h else None
    target["levels_src"] = src
    target["levels_est"] = bool(est)
    target.pop("levels_note", None)
    if name and not target.get("name"):
        target["name"] = name
    _refresh_candidate(target, params)


def apply_heights(area: dict, engine, features: List[dict], *, source: str = "upload", add_missing: bool = True,
                  min_add_height_m: float = 6.0, log: Log = print) -> dict:
    """Gabungkan tinggi gedung ke area (lapisan otomatis). Mengembalikan statistik. Area diubah langsung.

    Setiap fitur boleh membawa `source` sendiri (mis. "google" dari skrip Earth Engine) yang menimpa `source`.
    """
    params = area["params"]
    per = params.get("height_per_level_m")
    proj = LocalProjection(area["meta"]["center"][1], area["meta"]["center"][0])
    blds = area["buildings"]
    grid = _BGrid(blds, proj)
    by_id = {b["id"]: b for b in blds}
    stats = {"features": len(features), "updated": 0, "kept": 0, "added": 0, "unmatched": 0, "same_footprint": 0,
             "conflicts": 0, "religious_skipped": 0}
    added: List[dict] = []
    for f in features:
        src = f.get("source") or source
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
        levels = lv if lv else (levels_from_height(h, per) if h else None)
        if levels is None:
            continue
        est = not lv
        nid = None
        per_m = float(per or DEFAULT_PARAMS["height_per_level_m"])
        if target is None and add_missing and f.get("ring") and (h or levels * per_m) >= min_add_height_m:
            nid = stable_building_id(src, f["ring"], f.get("uid"))
            if nid in by_id:  # footprint sama dengan gedung tambahan yang sudah ada
                target = by_id[nid]
                stats["same_footprint"] += 1
        if target is not None:
            cur_src = target.get("levels_src", "default")
            religious_est = est and target.get("kind") == "ibadah"  # tinggi tempat ibadah = kubah/menara
            if (not religious_est and cur_src not in ("default", src)
                    and abs(int(target.get("levels") or 1) - int(levels)) >= CONFLICT_MIN_LEVELS):
                conflict = target.setdefault("levels_conflict", {})
                conflict[cur_src] = int(target.get("levels") or 1)
                conflict[src] = int(levels)
                stats["conflicts"] += 1
            if SOURCE_RANK.get(cur_src, 0) > SOURCE_RANK.get(src, 1):
                stats["kept"] += 1
                _refresh_candidate(target, params)
                continue
            if religious_est:  # tidak menebak lantai tempat ibadah dari tinggi -> tetap perlu survei
                stats["religious_skipped"] += 1
                continue
            _set_levels(target, levels, h, src, est, f.get("name") or "", params)
            stats["updated"] += 1
            continue
        if nid is None:
            stats["unmatched"] += 1
            continue
        nb = _new_building(area, engine, proj, f, ring_xy, levels, h, src, nid, est)
        if nb:
            added.append(nb)
            by_id[nid] = nb
            stats["added"] += 1
        else:
            stats["unmatched"] += 1
    blds.extend(added)
    area["meta"].setdefault("counts", {})["buildings"] = len(blds)
    log(f"  Tinggi gedung ({source}): {stats}")
    return stats


def _refresh_candidate(b: dict, params: dict) -> None:
    """Nama lama; sekarang hanya meneruskan ke tsunami.refresh_flags (satu sumber tier)."""
    tsunami.refresh_flags(b, params)


def _new_building(area: dict, engine, proj: LocalProjection, f: dict, ring_xy, levels: int, h: Optional[float],
                  source: str, bid: str, est: bool) -> Optional[dict]:
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
    road = engine.elev[u] + t * (engine.elev[v] - engine.elev[u])
    # Tanpa DEM di titik gedung: interpolasi jalan HANYA bila jalan dekat; selain itu elevasi tidak tersedia (0 m).
    ev_b = building_elevation(None, road, s["perp_m"], area["params"])
    b = {"id": bid, "name": f.get("name", ""), "kind": "tidak_diketahui", "btag": "", "osm_unsafe": "",
         "levels": int(levels),
         "levels_src": source, "levels_est": bool(est), "height_m": round(h, 1) if h else None, "basement": 0,
         "structure": "tidak_tahu", "area_m2": round(area_m2, 1), "lat": round(lat, 7), "lon": round(lon, 7),
         "poly": [[round(lo, 7), round(la, 7)] for la, lo in (proj.to_latlon(x, y) for x, y in ring)],
         "geom": "external", "residents": 0.0, "edge": e, "t": round(t, 4), "perp_m": round(s["perp_m"], 1),
         **ev_b, "hazard": int(engine.hazard[near]), "added_from": source}
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

    Bangunan yang sudah punya data lantai dari OSM/survei tidak diminta. Bila add_missing, poligon Open Buildings V3
    (kepercayaan >= 0,75) ikut diambil, tetapi HANYA yang bersinggungan dengan piksel setinggi >= min_add_height_m:
    rata-rata tinggi tidak dihitung untuk puluhan ribu rumah rendah yang pasti dibuang.
    BELUM diuji dengan Earth Engine asli (hanya modul tiruan).
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
        res = height.reduceRegions(collection=fc, reducer=ee.Reducer.mean(), scale=4, tileScale=4).getInfo()
        for f in res.get("features", []):
            p = f.get("properties") or {}
            h = parse_height_m(p.get("mean"))
            if h is not None and h > 0:
                out.append({"bid": p.get("bid"), "height": h, "levels": None, "name": "", "source": "google"})
    if add_missing:
        tall = height.gte(min_add_height_m).selfMask()
        blobs = tall.reduceToVectors(geometry=region, scale=8, geometryType="polygon", eightConnected=True,
                                     maxPixels=int(1e9), bestEffort=True)
        v3 = (ee.FeatureCollection(V3_ASSET).filterBounds(blobs)
              .filter(ee.Filter.gte("confidence", 0.75)).filter(ee.Filter.gte("area_in_meters", 40)))
        res = (height.reduceRegions(collection=v3, reducer=ee.Reducer.mean(), scale=4, tileScale=4)
               .filter(ee.Filter.gte("mean", min_add_height_m)).limit(max_new).getInfo())
        for f in res.get("features", []):
            g = f.get("geometry") or {}
            props = f.get("properties") or {}
            h = parse_height_m(props.get("mean"))
            ring = _ring(g["coordinates"][0]) if g.get("type") == "Polygon" and g.get("coordinates") else None
            if ring and h:
                out.append({"ring": ring, "height": h, "levels": None, "name": "", "source": "google",
                            "uid": props.get("full_plus_code")})
    log(f"  Earth Engine: {len(out)} nilai tinggi diterima.")
    return out
