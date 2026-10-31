"""Membangun data area (graf jalan kaki, bangunan, zona bahaya) dari data OpenStreetMap.

Masukan berformat sama dengan keluaran Overpass API (`out geom;`), sehingga
pipeline yang sama dipakai untuk data asli (tools/prepare_area.py) maupun
desa sintetis (backend/synthetic.py).
"""
from __future__ import annotations

import datetime as _dt
import math
from collections import deque
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .geo import (LocalProjection, SegmentGrid, haversine_m, point_in_polygon,
                  polygon_area_m2, polygon_centroid)
from .speed import FLOOR_HEIGHT_M, climb_time_s
from .tsunami import structure_from_tags

# Jalan yang tidak boleh/tidak realistis dilalui pejalan kaki.
EXCLUDED_HIGHWAY = {
    "motorway", "motorway_link", "construction", "proposed", "abandoned",
    "platform", "raceway", "bus_guideway", "escape", "razed", "corridor",
}
PATH_HIGHWAY = {"footway", "path", "pedestrian", "track", "bridleway", "cycleway"}
RESIDENTIAL_BUILDING = {
    "yes", "house", "residential", "detached", "semidetached_house", "terrace",
    "apartments", "hut", "bungalow", "farm",
}

DEFAULT_PARAMS = {
    # Aturan zona bahaya cadangan bila peta bahaya resmi belum tersedia.
    # NILAI INI ASUMSI DEMO: ganti dengan peta bahaya InaRISK/BMKG/BPBD.
    "hazard_elev_threshold_m": 20.0,
    "hazard_max_coast_dist_m": 3000.0,
    "safe_buffer_m": 2.0,          # target aman = elevasi >= ambang + max(buffer, galat DEM)
    # Galat vertikal DEM global (SRTM/Copernicus) di pesisir datar bisa beberapa meter: target aman
    # dinaikkan sebesar ini agar "tanah tinggi" tidak ternyata lebih rendah dari perkiraan. ASUMSI.
    "dem_uncertainty_m": 5.0,
    # Skenario rencana untuk gedung evakuasi vertikal bila peringatan tidak memuat tinggi gelombang
    # (BMKG "Awas" = > 3 m). ASUMSI DEMO: ganti dengan tinggi rencana dari kajian risiko BPBD/InaRISK.
    "tsunami_design_height_m": 10.0,
    "underpass_penalty": 10.0,     # terowongan/underpass di zona bahaya: tergenang lebih dulu
    "bridge_penalty": 3.0,         # jembatan dekat pantai: tsunami naik lewat sungai, jembatan bisa hanyut
    "bridge_coast_m": 1500.0,
    "hazard_edge_buffer_m": 50.0,  # peta bahaya resmi: target aman minimal sejauh ini dari batas zona
    "persons_per_house": 4.0,      # asumsi demo; ganti dengan data kependudukan desa
    "vulnerable_share": 0.15,      # asumsi demo; proporsi lansia/disabilitas/balita
    "shelter_min_levels": 2,
    "shelter_usable_factor": 0.5,  # FEMA P-646: 50-85% luas kotor dapat dipakai
    "space_per_person_m2": 0.93,   # FEMA P-646: 10 sq ft per orang
    "default_eta_min": 20.0,       # asumsi demo bila peringatan tidak memuat estimasi
}

ElevationProvider = Callable[[Sequence[Tuple[float, float]]], List[float]]
AREA_SCHEMA = 2  # 2: sumber jumlah lantai, struktur, basement, tanda ruas (terowongan/jembatan)


def upgrade_area(area: dict) -> dict:
    """Lengkapi data area versi lama (dibuat sebelum skema 2) agar tetap bisa dipakai."""
    if area.get("meta", {}).get("schema", 1) >= AREA_SCHEMA:
        return area
    for k, v in DEFAULT_PARAMS.items():
        area.setdefault("params", {}).setdefault(k, v)
    for b in area.get("buildings", []):
        b.setdefault("levels_src", "osm" if b.get("levels", 1) > 1 else "default")
        b.setdefault("height_m", None)
        b.setdefault("basement", 0)
        b.setdefault("structure", "tidak_tahu")
    area["edges"].setdefault("flag", [""] * len(area["edges"]["u"]))
    area["meta"]["schema"] = AREA_SCHEMA
    return area


def _walkable(tags: dict) -> bool:
    hw = tags.get("highway")
    if not hw or hw in EXCLUDED_HIGHWAY:
        return False
    if tags.get("foot") == "no" or tags.get("access") in ("private", "no"):
        return False
    if tags.get("area") == "yes":
        return False
    return True


def _edge_kind(tags: dict) -> str:
    hw = tags.get("highway", "")
    if hw == "steps":
        return "steps"
    if hw in PATH_HIGHWAY:
        return "path"
    return "road"


def _building_kind(tags: dict) -> str:
    b = tags.get("building", "")
    amenity = tags.get("amenity", "")
    if amenity == "place_of_worship" or b in ("mosque", "church", "temple", "chapel"):
        return "ibadah"
    if amenity in ("school", "college", "university") or b in ("school", "college", "university"):
        return "sekolah"
    if amenity in ("townhall", "police", "fire_station") or b in ("government", "public", "civic"):
        return "kantor"
    if amenity in ("hospital", "clinic", "doctors") or b in ("hospital", "clinic"):
        return "kesehatan"
    if tags.get("tourism") in ("hotel", "guest_house") or b == "hotel":
        return "hotel"
    if b in RESIDENTIAL_BUILDING:
        return "rumah"
    return "lainnya"


def _num(val) -> Optional[float]:
    try:
        return float(str(val).split(";")[0].replace(",", ".").replace("m", "").strip())
    except (TypeError, ValueError):
        return None


def levels_from_height(h: float) -> int:
    """Jumlah lantai dari tinggi gedung; dibulatkan ke bawah (lebih aman daripada melebih-lebihkan)."""
    return max(1, int(h // FLOOR_HEIGHT_M))


def _levels(tags: dict) -> Tuple[int, str, Optional[float]]:
    """(jumlah lantai, sumber, tinggi m). Sumber: osm | osm_height | google | upload | default."""
    h = _num(tags.get("height")) if tags.get("height") else None
    for key in ("building:levels", "levels"):
        v = _num(tags.get(key)) if tags.get(key) else None
        if v is not None and v >= 1:
            return max(1, int(round(v))), "osm", h
    if h is not None and h > 0:
        src = tags.get("sempat:height_src") or "osm_height"
        return levels_from_height(h), src, h
    return 1, "default", None


def _underground(tags: dict) -> int:
    v = _num(tags.get("building:levels:underground")) if tags.get("building:levels:underground") else None
    if v and v > 0:
        return int(round(v))
    if tags.get("location") == "underground" or (_num(tags.get("layer")) or 0) < 0:
        return 1
    if tags.get("parking") == "underground":
        return 1
    return 0


def _edge_flag(tags: dict) -> str:
    """'under' = terowongan/underpass, 'bridge' = jembatan, '' = biasa."""
    if tags.get("tunnel") in ("yes", "building_passage", "culvert", "flooded") or tags.get("location") == "underground" \
            or (_num(tags.get("layer")) or 0) < 0:
        return "under"
    if tags.get("bridge") and tags.get("bridge") != "no":
        return "bridge"
    return ""


def build_area(overpass: dict, elevation: ElevationProvider, *, area_id: str, name: str,
               params: Optional[dict] = None, hazard_polygons: Optional[List[List[Tuple[float, float]]]] = None,
               official_points: Optional[List[dict]] = None, synthetic: bool = False,
               sources: Optional[List[str]] = None, display: Optional[dict] = None,
               log: Callable[[str], None] = print) -> dict:
    """Bangun struktur area dari elemen Overpass.

    hazard_polygons: daftar ring [(lon, lat), ...] dari peta bahaya resmi (opsional).
    """
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    elements = overpass.get("elements", [])

    # ---------- 1. Kumpulkan ruas jalan ----------
    osm_nodes: Dict[int, Tuple[float, float]] = {}
    raw_edges: List[Tuple[int, int, str, str]] = []
    buildings_raw: List[dict] = []
    coast_lines: List[List[Tuple[float, float]]] = []
    water_lines: List[List[Tuple[float, float]]] = []
    for el in elements:
        if el.get("type") != "way":
            continue
        tags = el.get("tags", {}) or {}
        geom = el.get("geometry") or []
        nids = el.get("nodes") or []
        coords = [(g["lat"], g["lon"]) for g in geom if g]
        if tags.get("highway") and _walkable(tags) and len(coords) == len(nids) and len(nids) >= 2:
            kind = _edge_kind(tags)
            flag = _edge_flag(tags)
            for nid, c in zip(nids, coords):
                osm_nodes[nid] = c
            for a, b in zip(nids[:-1], nids[1:]):
                if a != b:
                    raw_edges.append((a, b, kind, flag))
        elif tags.get("building") and len(coords) >= 3:
            buildings_raw.append({"id": f"w{el.get('id')}", "tags": tags, "coords": coords})
        elif tags.get("natural") == "coastline" and len(coords) >= 2:
            coast_lines.append(coords)
        elif tags.get("waterway") in ("river", "stream", "canal") and len(coords) >= 2:
            water_lines.append(coords)

    if not raw_edges:
        raise ValueError("Tidak ada ruas jalan yang bisa dilalui pejalan kaki di area ini.")

    # ---------- 2. Graf: indeks node, hapus duplikat, ambil komponen terbesar ----------
    adj: Dict[int, set] = {}
    edge_kind: Dict[Tuple[int, int], str] = {}
    edge_flag: Dict[Tuple[int, int], str] = {}
    for a, b, kind, flag in raw_edges:
        key = (a, b) if a < b else (b, a)
        prev = edge_kind.get(key)
        # Bila dua ruas berimpit, utamakan jenis yang paling mudah dilalui.
        rank = {"road": 0, "path": 1, "steps": 2}
        if prev is None or rank[kind] < rank[prev]:
            edge_kind[key] = kind
        # ...dan untuk tanda bahaya, yang paling tidak berisiko (ruas biasa mengalahkan terowongan).
        frank = {"": 0, "bridge": 1, "under": 2}
        if key not in edge_flag or frank[flag] < frank[edge_flag[key]]:
            edge_flag[key] = flag
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    # Komponen terhubung terbesar
    seen: set = set()
    best_comp: List[int] = []
    for start in adj:
        if start in seen:
            continue
        comp = []
        dq = deque([start])
        seen.add(start)
        while dq:
            x = dq.popleft()
            comp.append(x)
            for y in adj[x]:
                if y not in seen:
                    seen.add(y)
                    dq.append(y)
        if len(comp) > len(best_comp):
            best_comp = comp
    keep = set(best_comp)
    dropped = len(adj) - len(keep)
    if dropped:
        log(f"  {dropped} node di luar jaringan utama diabaikan (tidak terhubung).")
    osm_ids = sorted(keep)
    index = {nid: i for i, nid in enumerate(osm_ids)}
    lat = [osm_nodes[n][0] for n in osm_ids]
    lon = [osm_nodes[n][1] for n in osm_ids]

    eu: List[int] = []
    ev: List[int] = []
    elen: List[float] = []
    ekind: List[str] = []
    eflag: List[str] = []
    for (a, b), kind in edge_kind.items():
        if a in keep and b in keep:
            ia, ib = index[a], index[b]
            eu.append(ia)
            ev.append(ib)
            elen.append(round(haversine_m(lat[ia], lon[ia], lat[ib], lon[ib]), 2))
            ekind.append(kind)
            eflag.append(edge_flag.get((a, b), ""))

    lat0 = sum(lat) / len(lat)
    lon0 = sum(lon) / len(lon)
    proj = LocalProjection(lat0, lon0)

    # ---------- 3. Elevasi ----------
    log(f"  Mengambil elevasi untuk {len(lat)} node ...")
    elev = [float(e) for e in elevation(list(zip(lat, lon)))]

    # ---------- 4. Jarak ke garis pantai ----------
    coast_grid = SegmentGrid(cell_m=250.0)
    for line in coast_lines:
        pts = [proj.to_xy(a, b) for a, b in line]
        for (ax, ay), (bx, by) in zip(pts[:-1], pts[1:]):
            coast_grid.add(ax, ay, bx, by, None)
    coast_dist: List[float] = []
    for i in range(len(lat)):
        x, y = proj.to_xy(lat[i], lon[i])
        hit = coast_grid.nearest(x, y, max_radius_m=p["hazard_max_coast_dist_m"] + 2000)
        coast_dist.append(round(hit[0], 1) if hit else 1e9)

    # ---------- 5. Zona bahaya dan target aman ----------
    polys_xy = None
    if hazard_polygons:
        polys_xy = [[proj.to_xy(la, lo) for (lo, la) in ring] for ring in hazard_polygons]
    thr = p["hazard_elev_threshold_m"]
    dmax = p["hazard_max_coast_dist_m"]
    if polys_xy is not None:
        hazard, safe_target = _hazard_from_polygons([proj.to_xy(lat[i], lon[i]) for i in range(len(lat))],
                                                    polys_xy, p["hazard_edge_buffer_m"])
    else:
        hazard, safe_target = [], []
        margin = max(p["safe_buffer_m"], p.get("dem_uncertainty_m", 0.0))
        for i in range(len(lat)):
            hz = elev[i] < thr and coast_dist[i] < dmax
            hazard.append(1 if hz else 0)
            ok = (not hz) and (elev[i] >= thr + margin or coast_dist[i] >= dmax)
            safe_target.append(1 if ok else 0)
    if not any(safe_target):
        log("  PERINGATAN: tidak ada node aman di area ini. Perluas batas area ke arah daratan tinggi.")

    # ---------- 6. Bangunan ----------
    edge_grid = SegmentGrid(cell_m=80.0)
    nxy = [proj.to_xy(lat[i], lon[i]) for i in range(len(lat))]
    for e in range(len(eu)):
        ax, ay = nxy[eu[e]]
        bx, by = nxy[ev[e]]
        edge_grid.add(ax, ay, bx, by, e)
    buildings: List[dict] = []
    for b in buildings_raw:
        coords = b["coords"]
        if coords[0] == coords[-1]:
            coords = coords[:-1]
        xy = [proj.to_xy(a, c) for a, c in coords]
        area_m2 = polygon_area_m2(xy)
        if area_m2 < 8:  # terlalu kecil untuk bangunan hunian
            continue
        cx, cy = polygon_centroid(xy)
        clat, clon = proj.to_latlon(cx, cy)
        hit = edge_grid.nearest(cx, cy, max_radius_m=600)
        if hit is None:
            continue
        d, t, _, _, e = hit
        tags = b["tags"]
        kind = _building_kind(tags)
        levels, levels_src, height_m = _levels(tags)
        structure = structure_from_tags(tags)
        residents = p["persons_per_house"] if kind == "rumah" else 0.0
        if kind == "rumah" and tags.get("building") == "apartments":
            residents = p["persons_per_house"] * max(1, levels) * max(1.0, area_m2 / 120.0)
        candidate = levels >= p["shelter_min_levels"] and kind != "rumah" and structure not in ("bata", "ringan")
        capacity = 0
        if candidate:
            capacity = int(area_m2 * (levels - 1) * p["shelter_usable_factor"] / p["space_per_person_m2"])
        u, v = eu[e], ev[e]
        b_elev = elev[u] + t * (elev[v] - elev[u])
        if polys_xy is not None:
            b_hazard = int(any(point_in_polygon(cx, cy, ring) for ring in polys_xy))
        else:
            ch = coast_grid.nearest(cx, cy, max_radius_m=dmax + 2000)
            b_coast = ch[0] if ch else 1e9
            b_hazard = int(b_elev < thr and b_coast < dmax)
        buildings.append({
            "id": b["id"],
            "name": tags.get("name", ""),
            "kind": kind,
            "levels": levels,
            "levels_src": levels_src,
            "height_m": round(height_m, 1) if height_m is not None else None,
            "basement": _underground(tags),
            "structure": structure,
            "area_m2": round(area_m2, 1),
            "lat": round(clat, 7), "lon": round(clon, 7),
            "poly": [[round(c, 7), round(a, 7)] for a, c in coords],
            "residents": round(residents, 2),
            "candidate": candidate,
            "capacity": capacity,
            "climb_s": round(climb_time_s(levels), 1) if candidate else 0.0,
            "edge": e, "t": round(t, 4), "perp_m": round(d, 1),
            "elev": round(b_elev, 1),
            "hazard": b_hazard,
        })

    n_people = sum(b["residents"] for b in buildings)
    log(f"  Graf: {len(lat)} node, {len(eu)} ruas | bangunan: {len(buildings)} | estimasi penduduk: {n_people:.0f}")

    bbox = [min(lon), min(lat), max(lon), max(lat)]
    area = {
        "meta": {
            "id": area_id,
            "name": name,
            "schema": AREA_SCHEMA,
            "synthetic": synthetic,
            "created": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "bbox": bbox,
            "center": [lon0, lat0],
            "sources": sources or [],
            "hazard_method": "polygon" if hazard_polygons else "rule",
            "counts": {"nodes": len(lat), "edges": len(eu), "buildings": len(buildings),
                       "people": round(n_people)},
        },
        "params": p,
        "nodes": {
            "lat": [round(v, 7) for v in lat],
            "lon": [round(v, 7) for v in lon],
            "elev": [round(v, 1) for v in elev],
            "coast": coast_dist,
            "hazard": hazard,
            "safe": safe_target,
        },
        "edges": {"u": eu, "v": ev, "len": elen, "kind": ekind, "flag": eflag},
        "buildings": buildings,
        "coastline": [[[round(c, 7), round(a, 7)] for a, c in line] for line in coast_lines],
        "water": [[[round(c, 7), round(a, 7)] for a, c in line] for line in water_lines],
        "official_points": official_points or [],
        "display": display or {},
    }
    if hazard_polygons:
        area["hazard_polygons"] = [[[round(lo, 7), round(la, 7)] for lo, la in ring] for ring in hazard_polygons]
    return area


def _hazard_from_polygons(nxy: List[Tuple[float, float]], polys_xy: List[List[Tuple[float, float]]],
                          buffer_m: float) -> Tuple[List[int], List[int]]:
    """Zona bahaya dari poligon resmi; target aman = di luar poligon DAN minimal buffer_m dari batasnya."""
    border = SegmentGrid(cell_m=100.0)
    for ring in polys_xy:
        for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1]):
            border.add(ax, ay, bx, by, None)
    hazard, safe = [], []
    for x, y in nxy:
        hz = any(point_in_polygon(x, y, ring) for ring in polys_xy)
        hazard.append(1 if hz else 0)
        if hz:
            safe.append(0)
        else:
            hit = border.nearest(x, y, max_radius_m=buffer_m)
            safe.append(0 if hit is not None and hit[0] < buffer_m else 1)
    return hazard, safe


def apply_hazard_polygons(area: dict, rings: List[List[Tuple[float, float]]], source: str) -> dict:
    """Ganti zona bahaya area dengan poligon peta bahaya resmi (ring [(lon, lat), ...])."""
    if not rings:
        raise ValueError("Tidak ada poligon.")
    proj = LocalProjection(area["meta"]["center"][1], area["meta"]["center"][0])
    N = area["nodes"]
    polys_xy = [[proj.to_xy(la, lo) for (lo, la) in ring] for ring in rings]
    nxy = [proj.to_xy(a, b) for a, b in zip(N["lat"], N["lon"])]
    N["hazard"], N["safe"] = _hazard_from_polygons(nxy, polys_xy, float(area["params"].get("hazard_edge_buffer_m", 50)))
    for b in area["buildings"]:
        x, y = proj.to_xy(b["lat"], b["lon"])
        b["hazard"] = int(any(point_in_polygon(x, y, ring) for ring in polys_xy))
    area["hazard_polygons"] = [[[round(lo, 7), round(la, 7)] for lo, la in ring] for ring in rings]
    meta = area["meta"]
    meta["hazard_method"] = "polygon"
    meta["sources"] = [x for x in meta.get("sources", []) if not x.startswith("Zona bahaya:")] + [f"Zona bahaya: {source}"]
    return {"hazard_nodes": sum(N["hazard"]), "safe_nodes": sum(N["safe"]), "nodes": len(N["hazard"])}


def validate_area(area: dict) -> List[str]:
    """Pemeriksaan kewajaran data area; mengembalikan daftar peringatan."""
    warns = []
    n = len(area["nodes"]["lat"])
    for key in ("lon", "elev", "hazard", "safe", "coast"):
        if len(area["nodes"][key]) != n:
            warns.append(f"Panjang kolom nodes.{key} tidak sama dengan jumlah node.")
    m = len(area["edges"]["u"])
    for key in ("v", "len", "kind"):
        if len(area["edges"][key]) != m:
            warns.append(f"Panjang kolom edges.{key} tidak sama dengan jumlah ruas.")
    if not any(area["nodes"]["safe"]):
        warns.append("Tidak ada node aman.")
    long_edges = sum(1 for L in area["edges"]["len"] if L > 2000)
    if long_edges:
        warns.append(f"{long_edges} ruas lebih dari 2 km (periksa data).")
    if math.isnan(sum(area["nodes"]["elev"])):
        warns.append("Ada elevasi NaN.")
    return warns
