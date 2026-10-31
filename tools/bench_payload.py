"""Benchmark ukuran payload API per peran (mentah & gzip) pada area SINTETIS berbagai ukuran.

    python tools/bench_payload.py [--sizes 400,5000,20000] [--json out.json]

Area dibuat dari grid jalan 50 m dengan rumah 1 lantai (poligon 8 titik) dan 3 % gedung bertingkat. Ini perilaku
format data, BUKAN ukuran kota nyata: poligon OSM nyata bisa lebih kompleks. Yang diukur:
* /api/area lengkap (petugas/perencana) vs publik (warga, AreaCtx.public_bytes);
* /api/tables (tabel rute HP) — alokasi dimatikan (tanpa penduduk) agar ukuran tabel dasar saja yang diukur.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import area_builder as AB  # noqa: E402
from backend.app import AreaCtx  # noqa: E402
from backend.geo import LocalProjection  # noqa: E402

PROJ = LocalProjection(-0.95, 100.35)


def _geom(pts):
    return [{"lat": a, "lon": b} for a, b in (PROJ.to_latlon(x, y) for x, y in pts)]


def make_overpass(n_buildings: int, seed: int = 3) -> dict:
    rng = random.Random(seed)
    side = max(4, int(math.ceil(math.sqrt(n_buildings / 6.0))) + 1)  # ±6 bangunan per blok 50 m
    els, nid = [], 1
    for j in range(side):  # jalan horizontal & vertikal
        row = list(range(nid, nid + side))
        els.append({"type": "way", "id": 10_000 + j, "tags": {"highway": "residential"}, "nodes": row,
                    "geometry": _geom([(i * 50.0, j * 50.0) for i in range(side)])})
        nid += side
    for i in range(side):
        col = [1 + j * side + i for j in range(side)]
        els.append({"type": "way", "id": 20_000 + i, "tags": {"highway": "residential"}, "nodes": col,
                    "geometry": _geom([(i * 50.0, j * 50.0) for j in range(side)])})
    els.append({"type": "way", "id": 1, "tags": {"natural": "coastline"}, "nodes": [9_000_001, 9_000_002],
                "geometry": _geom([(-200, -300), (side * 50 + 200, -300)])})
    for k in range(n_buildings):
        cx, cy = rng.uniform(5, (side - 1) * 50 - 5), rng.uniform(5, (side - 1) * 50 - 5)
        r = rng.uniform(4, 7)
        tall = rng.random() < 0.03
        ring = [(cx + r * math.cos(t * math.pi / 4) * (1.0 + 0.1 * rng.random()),
                 cy + r * math.sin(t * math.pi / 4)) for t in range(8)]
        tags = {"building": "school", "building:levels": str(rng.randint(2, 6))} if tall else {"building": "house"}
        els.append({"type": "way", "id": 100_000 + k, "tags": tags, "geometry": _geom(ring + ring[:1])})
    return {"elements": els}


def measure(n: int) -> dict:
    t0 = time.perf_counter()
    area = AB.build_area(make_overpass(n), lambda pts: [2.0] * len(pts), area_id=f"bench{n}", name="bench",
                         synthetic=True, log=lambda *_: None)
    for b in area["buildings"]:
        b["residents"] = 0.0  # tabel dasar tanpa alokasi
    t_build = time.perf_counter() - t0
    t0 = time.perf_counter()
    c = AreaCtx(area)
    t_ctx = time.perf_counter() - t0
    lt = c.engine.live_tables(closures=set(), shelter_ids=[], points=[], alert_active=True, wave_m=3.0)
    lt.pop("_alloc_requests", None)
    tb = json.dumps(lt, separators=(",", ":")).encode()
    gz = lambda b: len(gzip.compress(b, 6))  # noqa: E731
    pub = json.loads(c.public_bytes)
    return {"buildings": len(area["buildings"]), "nodes": len(area["nodes"]["lat"]),
            "buildings_sent_public": pub["meta"]["buildings_sent"],
            "area_full_raw": len(c.area_bytes), "area_full_gzip": gz(c.area_bytes),
            "area_staff_raw": len(c.staff_bytes), "area_staff_gzip": gz(c.staff_bytes),
            "area_public_raw": len(c.public_bytes), "area_public_gzip": gz(c.public_bytes),
            "tables_raw": len(tb), "tables_gzip": gz(tb), "build_s": round(t_build, 2), "ctx_s": round(t_ctx, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="400,5000,20000")
    ap.add_argument("--json")
    a = ap.parse_args()
    rows = [measure(int(x)) for x in a.sizes.split(",")]
    kb = lambda v: f"{v / 1024:,.0f}".replace(",", ".")  # noqa: E731
    print("| bangunan | simpul | dikirim ke warga | sebelum (data lengkap) mentah / gzip (KB) | petugas/perencana "
          "mentah / gzip (KB) | warga mentah / gzip (KB) | tabel HP mentah / gzip (KB) |")
    print("|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['buildings']} | {r['nodes']} | {r['buildings_sent_public']} | {kb(r['area_full_raw'])} / "
              f"{kb(r['area_full_gzip'])} | {kb(r['area_staff_raw'])} / {kb(r['area_staff_gzip'])} | "
              f"{kb(r['area_public_raw'])} / {kb(r['area_public_gzip'])} | {kb(r['tables_raw'])} / {kb(r['tables_gzip'])} |")
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
