"""Desa sintetis untuk menguji sistem tanpa internet.

PERHATIAN: seluruh data di sini DIBUAT-BUAT (bukan desa nyata) dan diletakkan
di koordinat laut lepas agar tidak tertukar dengan lokasi sungguhan. Gunakan
tools/prepare_area.py untuk membangun data desa asli dari OpenStreetMap.

Data sintetis dibentuk sebagai keluaran mirip Overpass API lalu diproses
oleh area_builder yang sama dengan data asli.
"""
from __future__ import annotations

import math
import random
from typing import Dict, List, Tuple

from .area_builder import build_area
from .geo import LocalProjection, SegmentGrid

LAT0, LON0 = -9.50, 112.50  # laut lepas, jauh dari daratan
NAME = "Desa Simulasi Pantai Selatan (DATA SINTETIS)"
AREA_ID = "desa-sintetis"

PROJ = LocalProjection(LAT0, LON0)


def coast_y(x: float) -> float:
    return 120.0 + 40.0 * math.sin(x / 300.0)


def river_x(y: float) -> float:
    return 1100.0 + 25.0 * math.sin(y / 180.0)


def elevation_xy(x: float, y: float) -> float:
    base = max(0.0, y - coast_y(x)) * 0.012
    ridge = 30.0 / (1.0 + math.exp(-(y - 1350.0) / 100.0))
    hill = 35.0 * math.exp(-((x - 1900.0) ** 2 + (y - 700.0) ** 2) / (2 * 250.0 ** 2))
    valley = -3.0 * math.exp(-((x - river_x(y)) ** 2) / (2 * 60.0 ** 2))
    micro = 0.6 * math.sin(x / 97.0) * math.cos(y / 83.0)
    return max(0.5, base + ridge + hill + valley + micro)


# ---------- Jaringan jalan (koordinat meter: x ke timur, y ke utara) ----------
J: Dict[str, Tuple[float, float]] = {
    # sisi barat sungai
    "Wc1": (150, 230), "Wc2": (400, 240), "Wc3": (800, 235), "Wc4": (1040, 225),
    "Wa1": (400, 500), "Wa2": (800, 500), "Wa3": (1040, 500),
    "Wb1": (400, 750), "Wb2": (800, 750), "Wb3": (1040, 760),
    "Wd1": (400, 1000), "Wd2": (800, 1000), "Wd3": (1040, 1000),
    "We1": (400, 1250), "We2": (800, 1250),
    "Wf1": (400, 1500), "Wf2": (800, 1500), "Wf3": (1040, 1500),
    "Wg1": (600, 1700),
    # sisi timur sungai
    "Ec1": (1160, 225), "Ec2": (1500, 250), "Ec3": (1900, 260), "Ec4": (2300, 280),
    "Ea1": (1160, 500), "Ea2": (1500, 500), "Ea3": (2250, 500),
    "Eb1": (1160, 800), "Eb2": (1500, 800),
    "Ed1": (1160, 1000), "Ed2": (1500, 1000), "Ed3": (2100, 1000),
    "Ef1": (1160, 1500), "Ef2": (1500, 1500), "Ef3": (2100, 1500),
    "Eh0": (1900, 700),  # puncak bukit timur
}

# (urutan titik, tag highway, nama, bridge?)
ROADS: List[Tuple[List, str, str, bool]] = [
    (["Wc1", "Wc2", "Wc3", "Wc4"], "secondary", "Jalan Pantai Barat", False),
    (["Wc2", "Wa1", "Wb1", "Wd1", "We1", "Wf1"], "residential", "Jalan Mawar", False),
    (["Wc3", "Wa2", "Wb2", "Wd2", "We2", "Wf2"], "residential", "Jalan Melati", False),
    (["Wa1", "Wa2", "Wa3"], "tertiary", "Jalan Desa", False),
    (["Wb1", "Wb2", "Wb3"], "residential", "Jalan Kenanga", False),
    (["Wd1", "Wd2", "Wd3"], "residential", "Jalan Anggrek", False),
    (["We1", "We2"], "residential", "Jalan Bukit Utara", False),
    (["Wf1", "Wf2", "Wf3"], "tertiary", "Jalan Lingkar Utara", False),
    (["Wf1", "Wg1", "Wf2"], "footway", "Jalan Setapak Lapangan", False),
    (["Wc4", "Wa3", "Wb3", "Wd3"], "residential", "Jalan Tepi Sungai Barat", False),
    (["Wa3", "Ea1"], "tertiary", "Jembatan Desa", True),
    (["Wf3", "Ef1"], "tertiary", "Jembatan Utara", True),
    (["Ec1", "Ec2", "Ec3", "Ec4"], "secondary", "Jalan Pantai Timur", False),
    (["Ec1", "Ea1", "Eb1", "Ed1", "Ef1"], "residential", "Jalan Tepi Sungai Timur", False),
    (["Ec2", "Ea2", "Eb2", "Ed2", "Ef2"], "residential", "Jalan Cempaka", False),
    (["Ea1", "Ea2"], "tertiary", "Jalan Desa", False),
    (["Ea2", (1700, 470), (2000, 440), "Ea3"], "residential", "Jalan Kaki Bukit", False),
    (["Ec4", "Ea3", (2200, 800), "Ed3"], "residential", "Jalan Timur", False),
    (["Eb1", "Eb2"], "residential", "Jalan Flamboyan", False),
    (["Ed1", "Ed2", "Ed3"], "residential", "Jalan Kamboja", False),
    (["Ef1", "Ef2", "Ef3"], "tertiary", "Jalan Lingkar Utara", False),
    (["Ed3", "Ef3"], "residential", "Jalan Timur", False),
    # jalur naik bukit timur
    (["Ea2", (1650, 560), (1780, 640), "Eh0"], "path", "Jalur Bukit Barat", False),
    (["Ec3", (1900, 400), (1905, 520)], "path", "Jalur Bukit Selatan", False),
    ([(1905, 520), (1902, 600), "Eh0"], "steps", "Tangga Bukit", False),
    (["Ed3", (2020, 860), "Eh0"], "path", "Jalur Bukit Utara", False),
]

SPECIAL_BUILDINGS = [
    # (x, y, lebar, panjang, tags)
    (430, 300, 20, 20, {"building": "mosque", "amenity": "place_of_worship", "name": "Masjid Al-Ikhlas", "building:levels": "2"}),
    (300, 790, 12, 12, {"building": "yes", "amenity": "place_of_worship", "name": "Musala Nurul Huda", "building:levels": "1"}),
    (760, 560, 30, 12, {"building": "school", "amenity": "school", "name": "SD Negeri 1", "building:levels": "2"}),
    (1460, 560, 40, 14, {"building": "school", "amenity": "school", "name": "SMP Negeri 2", "building:levels": "3"}),
    (2000, 320, 30, 20, {"building": "hotel", "tourism": "hotel", "name": "Hotel Pantai Indah", "building:levels": "3"}),
    (840, 960, 20, 15, {"building": "government", "amenity": "townhall", "name": "Kantor Desa", "building:levels": "2"}),
    (900, 285, 25, 15, {"building": "warehouse", "name": "Gudang TPI", "building:levels": "1"}),
    (1545, 960, 18, 12, {"building": "yes", "amenity": "clinic", "name": "Puskesmas Pembantu", "building:levels": "1"}),
]

OFFICIAL_POINTS = [
    {"name": "Titik Kumpul Bukit Timur", "kind": "titik_kumpul", "xy": (1900, 700)},
    {"name": "Titik Kumpul Lapangan Utara", "kind": "titik_kumpul", "xy": (600, 1700)},
]


def _ll(x: float, y: float) -> Tuple[float, float]:
    return PROJ.to_latlon(x, y)


def generate_overpass(seed: int = 7) -> dict:
    rng = random.Random(seed)
    elements: List[dict] = []
    node_ids: Dict[Tuple[float, float], int] = {}
    next_id = [1000]
    way_id = [1]

    def nid(pt: Tuple[float, float]) -> int:
        key = (round(pt[0], 2), round(pt[1], 2))
        if key not in node_ids:
            node_ids[key] = next_id[0]
            next_id[0] += 1
        return node_ids[key]

    road_grid = SegmentGrid(cell_m=60.0)
    road_polylines: List[Tuple[List[Tuple[float, float]], str]] = []

    for seq, hw, name, bridge in ROADS:
        pts = [J[p] if isinstance(p, str) else p for p in seq]
        dense: List[Tuple[float, float]] = [pts[0]]
        for (ax, ay), (bx, by) in zip(pts[:-1], pts[1:]):
            L = math.hypot(bx - ax, by - ay)
            n = max(1, int(L // 35))
            for k in range(1, n + 1):
                f = k / n
                dense.append((ax + f * (bx - ax), ay + f * (by - ay)))
        ids = [nid(p) for p in dense]
        tags = {"highway": hw, "name": name}
        if bridge:
            tags["bridge"] = "yes"
        elements.append({
            "type": "way", "id": way_id[0], "nodes": ids, "tags": tags,
            "geometry": [{"lat": _ll(*p)[0], "lon": _ll(*p)[1]} for p in dense],
        })
        way_id[0] += 1
        for (ax, ay), (bx, by) in zip(dense[:-1], dense[1:]):
            road_grid.add(ax, ay, bx, by, hw)
        road_polylines.append((dense, hw))

    # ---------- Bangunan ----------
    placed: List[Tuple[float, float]] = []

    def rect(cx: float, cy: float, w: float, h: float, ang: float) -> List[Tuple[float, float]]:
        ca, sa = math.cos(ang), math.sin(ang)
        out = []
        for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
            out.append((cx + dx * ca - dy * sa, cy + dx * sa + dy * ca))
        out.append(out[0])
        return out

    def add_building(poly: List[Tuple[float, float]], tags: dict) -> None:
        elements.append({
            "type": "way", "id": way_id[0], "tags": tags,
            "nodes": [],
            "geometry": [{"lat": _ll(*p)[0], "lon": _ll(*p)[1]} for p in poly],
        })
        way_id[0] += 1

    for x, y, w, h, tags in SPECIAL_BUILDINGS:
        add_building(rect(x, y, w, h, 0.0), dict(tags))
        placed.append((x, y))

    for dense, hw in road_polylines:
        if hw in ("path", "steps", "footway"):
            continue
        for (ax, ay), (bx, by) in zip(dense[:-1], dense[1:]):
            L = math.hypot(bx - ax, by - ay)
            if L < 1:
                continue
            ux, uy = (bx - ax) / L, (by - ay) / L
            nx, ny = -uy, ux
            ang = math.atan2(uy, ux)
            for side in (-1, 1):
                if rng.random() < 0.28:
                    continue
                mx, my = (ax + bx) / 2, (ay + by) / 2
                cx, cy = mx + side * nx * 15.0, my + side * ny * 15.0
                if cy < coast_y(cx) + 30 or cy > 1180:
                    continue
                if abs(cx - river_x(cy)) < 55:
                    continue
                if elevation_xy(cx, cy) > 30:
                    continue
                hit = road_grid.nearest(cx, cy, max_radius_m=60)
                if hit and hit[0] < 9.5:
                    continue
                if any((cx - px) ** 2 + (cy - py) ** 2 < 15.0 ** 2 for px, py in placed[-40:]) or \
                        any((cx - px) ** 2 + (cy - py) ** 2 < 32.0 ** 2 for px, py in placed[:len(SPECIAL_BUILDINGS)]):
                    continue
                w = rng.uniform(7.5, 10.0)
                h = rng.uniform(9.0, 12.0)
                add_building(rect(cx, cy, w, h, ang), {"building": "house"})
                placed.append((cx, cy))

    # ---------- Garis pantai dan sungai ----------
    coast = [(x, coast_y(x)) for x in range(-200, 2701, 50)]
    elements.append({"type": "way", "id": way_id[0], "tags": {"natural": "coastline"}, "nodes": [],
                     "geometry": [{"lat": _ll(*p)[0], "lon": _ll(*p)[1]} for p in coast]})
    way_id[0] += 1
    river = [(river_x(y), y) for y in range(int(coast_y(1100)) - 10, 1901, 40)]
    elements.append({"type": "way", "id": way_id[0], "tags": {"waterway": "river", "name": "Kali Simulasi"}, "nodes": [],
                     "geometry": [{"lat": _ll(*p)[0], "lon": _ll(*p)[1]} for p in river]})
    return {"elements": elements}


def elevation_provider(points):
    out = []
    for lat, lon in points:
        x, y = PROJ.to_xy(lat, lon)
        out.append(round(elevation_xy(x, y), 1))
    return out


def build(log=print) -> dict:
    overpass = generate_overpass()
    sea = [(x, coast_y(x)) for x in range(-200, 2701, 50)] + [(2700, -700), (-200, -700)]
    sea_ll = [[round(_ll(x, y)[1], 7), round(_ll(x, y)[0], 7)] for x, y in sea]
    sea_ll.append(sea_ll[0])
    official = []
    for p in OFFICIAL_POINTS:
        la, lo = _ll(*p["xy"])
        official.append({"name": p["name"], "kind": p["kind"], "lat": round(la, 7), "lon": round(lo, 7)})
    area = build_area(
        overpass, elevation_provider, area_id=AREA_ID, name=NAME, synthetic=True,
        official_points=official,
        sources=["DATA SINTETIS untuk uji sistem — bukan desa nyata"],
        display={"sea": [sea_ll]}, log=log,
    )
    return area


def seed_points() -> List[dict]:
    """Contoh titik bantuan untuk simulasi (ditandai sebagai contoh)."""
    a = _ll(620, 1690)
    b = _ll(1880, 725)
    return [
        {"kind": "posko_sar", "name": "Posko SAR Lapangan Utara (contoh)", "lat": a[0], "lon": a[1],
         "status": "kosong", "services": ["medis", "air"], "capacity": 300},
        {"kind": "dapur_umum", "name": "Dapur Umum Bukit Timur (contoh)", "lat": b[0], "lon": b[1],
         "status": "agak_ramai", "services": ["makanan", "air"], "capacity": 150},
    ]
