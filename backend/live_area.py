"""Membangun area SEMPAT secara otomatis di sekitar posisi mana pun (data nyata).

Alur: posisi warga -> kotak ±2 km -> Overpass (jalan, pantai, sungai, fasilitas)
-> elevasi DEM -> area_builder yang sama dengan data desa -> disimpan sebagai cache.
Bila di kotak itu belum ada tempat aman yang terjangkau (mis. dataran rendah luas),
kotak diperbesar dan digeser menjauhi pantai lalu dibangun ulang.
"""
from __future__ import annotations

import math
import os
from typing import Callable, List, Optional, Tuple

from . import osm_fetch
from .area_builder import build_area
from .geo import haversine_m

GRID_DEG = 0.01            # posisi dibulatkan ke grid ±1,1 km agar cache bisa dipakai bersama
HALF_M = 2000.0            # setengah sisi kotak percobaan pertama
HALF_BIG_M = 3500.0        # percobaan kedua, digeser menjauhi pantai
SHIFT_M = 1500.0
CORE_MARGIN_M = 600.0      # posisi dianggap tercakup bila >= 600 m dari tepi kotak

Source = Callable[[Tuple[float, float, float, float], bool], Tuple[dict, Callable]]


def bbox_around(lat: float, lon: float, half_m: float) -> Tuple[float, float, float, float]:
    dlat = half_m / 111320.0
    dlon = half_m / (111320.0 * max(0.2, math.cos(math.radians(lat))))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)


def live_id(lat: float, lon: float) -> str:
    a, b = int(round(lat / GRID_DEG)), int(round(lon / GRID_DEG))
    return f"live-{'s' if a < 0 else 'n'}{abs(a):05d}-{'w' if b < 0 else 'e'}{abs(b):05d}"


def covers(meta: dict, lat: float, lon: float, margin_m: float = CORE_MARGIN_M) -> bool:
    w, s, e, n = meta["bbox"]
    if not (s <= lat <= n and w <= lon <= e):
        return False
    dy = min(lat - s, n - lat) * 111320.0
    dx = min(lon - w, e - lon) * 111320.0 * math.cos(math.radians(lat))
    return min(dx, dy) >= margin_m


def internet_source(cache_dir, log) -> Source:
    def src(bbox, buildings):
        q = osm_fetch.overpass_query(bbox, buildings=buildings)
        data = osm_fetch.fetch_overpass(q, log=log)
        return data, osm_fetch.default_dem(cache_dir, log=log)
    return src


def _test_source() -> Optional[Source]:
    """Mode uji tanpa internet: desa sintetis dipindahkan ke posisi yang diminta (JANGAN untuk demo)."""
    if os.environ.get("SEMPAT_FAKE_OSM") != "1":
        return None
    from . import synthetic

    def src(bbox, buildings):
        s, w, n, e = bbox
        clat, clon = (s + n) / 2, (w + e) / 2
        base = synthetic.generate_overpass()
        slat, slon = synthetic.PROJ.to_latlon(1200, 800)
        dlat, dlon = clat - slat, clon - slon
        els = []
        for el in base["elements"]:
            el = dict(el)
            if el.get("geometry"):
                el["geometry"] = [{"lat": g["lat"] + dlat, "lon": g["lon"] + dlon} for g in el["geometry"]]
            if not buildings and (el.get("tags") or {}).get("building"):
                continue
            els.append(el)
        a, b = synthetic.PROJ.to_latlon(620, 1690)
        els.append({"type": "node", "id": 1, "lat": a + dlat, "lon": b + dlon,
                    "tags": {"amenity": "clinic", "name": "Puskesmas Uji (SINTETIS)"}})

        def dem(points):
            return synthetic.elevation_provider([(la - dlat, lo - dlon) for la, lo in points])
        dem.name = "Elevasi: SINTETIS (mode uji)"
        return {"elements": els}, dem
    return src


def _reachable_safe(area: dict, lat: float, lon: float) -> bool:
    from .engine import Engine
    eng = Engine(area)
    r = eng.route(lat, lon, mode="aman", group="umum", closures=set(), shelter_ids=[], points=[],
                  alert_active=False, alternatives=1)
    return bool(r.get("ok") and (r.get("already_safe") or r.get("routes")))


def _inland_shift(area: dict, lat: float, lon: float, dist_m: float) -> Tuple[float, float]:
    best = None
    for line in area.get("coastline") or []:
        for clon, clat in line:
            d = haversine_m(lat, lon, clat, clon)
            if best is None or d < best[0]:
                best = (d, clat, clon)
    if best is None or best[0] < 1:
        return lat, lon
    _, clat, clon = best
    kx = 111320.0 * math.cos(math.radians(lat))
    vx, vy = (lon - clon) * kx, (lat - clat) * 111320.0
    L = math.hypot(vx, vy)
    return lat + vy / L * dist_m / 111320.0, lon + vx / L * dist_m / kx


def build_bbox(bbox: Tuple[float, float, float, float], *, area_id: str, name: str, buildings: bool,
               source: Source, log=print, kind: str = "live") -> dict:
    data, dem = source(bbox, buildings)
    area = build_area(data, dem, area_id=area_id, name=name, log=log,
                      sources=["Jalan & pantai: © kontributor OpenStreetMap (ODbL)",
                               getattr(dem, "used", None) or getattr(dem, "name", "Elevasi DEM")])
    area["facilities"] = osm_fetch.extract_facilities(data)
    s, w, n, e = bbox
    area["meta"].update({"kind": kind, "live": True, "request_bbox": [w, s, e, n]})
    # bbox untuk cakupan = kotak permintaan (bukan sebaran node) agar konsisten
    area["meta"]["bbox"] = [w, s, e, n]
    return area


def build_around(lat: float, lon: float, *, source: Optional[Source] = None, cache_dir=None,
                 log=print) -> dict:
    src = source or _test_source() or internet_source(cache_dir, log)
    clat, clon = round(lat / GRID_DEG) * GRID_DEG, round(lon / GRID_DEG) * GRID_DEG
    aid = live_id(lat, lon)
    name = None if os.environ.get("SEMPAT_FAKE_OSM") == "1" else osm_fetch.reverse_name(lat, lon)
    name = name or f"Sekitar {lat:.3f}, {lon:.3f}"
    log(f"Membangun peta di sekitar {lat:.5f}, {lon:.5f} ({name}) ...")
    area = build_bbox(bbox_around(clat, clon, HALF_M), area_id=aid, name=name, buildings=False,
                      source=src, log=log)
    if area.get("coastline") and not _reachable_safe(area, lat, lon):
        log("  Belum ada tempat aman terjangkau; perluas peta ke arah daratan ...")
        slat, slon = _inland_shift(area, lat, lon, SHIFT_M)
        big = bbox_around(slat, slon, HALF_BIG_M)  # pusat digeser 1,5 km ke arah daratan
        # pastikan posisi warga tetap tercakup
        s, w, n, e = big
        big = (min(s, lat - 0.008), min(w, lon - 0.008), max(n, lat + 0.008), max(e, lon + 0.008))
        area2 = build_bbox(big, area_id=aid, name=name, buildings=False, source=src, log=log)
        area2["meta"]["expanded"] = True
        area = area2
    area["meta"]["built_for"] = [round(lon, 6), round(lat, 6)]
    return area
