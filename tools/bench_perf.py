"""Skenario performa SEMPAT yang bisa diulang: server + peramban, area kecil sampai besar.

    python tools/bench_perf.py [--sizes 400,5000,20000] [--profiles desktop,android] [--json out.json]

Untuk setiap ukuran dibuat area SINTETIS (grid jalan 50 m, rumah 8 titik, 3 % gedung bertingkat; lihat
tools/bench_payload.py) di direktori data sementara, server dijalankan, lalu diukur:

Server (HTTP lokal, tanpa throttling):
  * waktu & ukuran /api/area (warga dan petugas; dingin = permintaan pertama setelah area dimuat, hangat = ulang),
    /api/tables, /api/areas/{id}/time-grid, dan tambalan /api/area/patch setelah satu survei.
Peramban (tests/perf/perf.js, Chromium Playwright):
  * waktu sampai area terunduh dan sampai peta siap (tabel rute ada, peta `idle`) untuk warga/petugas/perencana;
  * waktu membangun GeoJSON gedung, heap JS, dan laju bingkai saat menggeser peta 3D di zoom 17.
  * profil "android" = EMULASI kelas menengah: CPU 4x lebih lambat + jaringan 4G lambat (RTT 150 ms, 1,6 Mbps
    turun, 750 kbps naik), viewport 390x844. Ini BUKAN pengukuran di HP sungguhan; protokolnya ada di
    docs/UJI_PERANGKAT.md.

Angka bergantung mesin; yang bermakna adalah perbandingan antarukuran & antarversi pada mesin yang sama.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import bench_payload as BP  # noqa: E402
from backend import area_builder as AB  # noqa: E402
from backend.app import _meta_summary  # noqa: E402
from backend.geo import LocalProjection  # noqa: E402

# Jauh dari desa sintetis bawaan (-0.94, 100.35) agar /api/locate warga memilih area uji ini.
CENTER = (-3.80, 102.26)
BP.PROJ = LocalProjection(*CENTER)


def _elev(pts):
    """Ketinggian naik 2 m per 100 m ke darat (garis pantai uji di y = -300 m): ada zona bahaya DAN zona aman,
    jadi tabel rute, alokasi, dan grid waktu benar-benar dihitung (bukan kasus kosong)."""
    out = []
    for p in pts:
        lat, lon = (p["lat"], p["lon"]) if isinstance(p, dict) else (p[0], p[1])
        _, y = BP.PROJ.to_xy(lat, lon)
        out.append(round(max(0.5, (y + 300.0) * 0.02), 2))
    return out


def write_area(data: Path, n: int) -> dict:
    area = AB.build_area(BP.make_overpass(n), _elev, area_id=f"bench{n}",
                         name=f"Uji performa {n} bangunan (SINTETIS)", synthetic=True, log=lambda *_: None)
    d = data / "areas" / area["meta"]["id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "area.json").write_text(json.dumps(area, separators=(",", ":")))
    (d / "meta.json").write_text(json.dumps(_meta_summary(area["meta"])))
    return area


def http(method: str, url: str, body=None, token=None, gz=True):
    h = {"Accept-Encoding": "gzip"} if gz else {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    t0 = time.perf_counter()
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h, method=method), timeout=600) as r:
        raw = r.read()
        enc = r.headers.get("Content-Encoding")
    dt = time.perf_counter() - t0
    body_bytes = gzip.decompress(raw) if enc == "gzip" else raw
    return dt, len(raw), body_bytes


def server_metrics(base: str, aid: str, area: dict) -> dict:
    tok = json.loads(http("POST", base + "/api/auth/login", {"username": "petugas", "password": "petugas123"})[2])["token"]
    out = {}
    q = f"?area={aid}"
    for name, t in (("publik", None), ("petugas", tok)):
        cold = http("GET", base + "/api/area" + q, token=t)
        warm = http("GET", base + "/api/area" + q, token=t)
        out[f"area_{name}"] = {"cold_s": round(cold[0], 3), "warm_s": round(warm[0], 3), "gzip_B": cold[1],
                               "raw_B": len(cold[2])}
    tb = http("GET", base + "/api/tables" + q)
    out["tables"] = {"s": round(tb[0], 3), "gzip_B": tb[1], "raw_B": len(tb[2])}
    tg = http("GET", f"{base}/api/areas/{aid}/time-grid?mode=aman&group=umum", token=tok)
    tg2 = http("GET", f"{base}/api/areas/{aid}/time-grid?mode=aman&group=umum", token=tok)
    out["time_grid"] = {"cold_s": round(tg[0], 3), "cached_s": round(tg2[0], 3), "gzip_B": tg[1],
                        "runs": len(json.loads(tg[2])["runs"])}
    meta = json.loads(http("GET", base + "/api/area" + q)[2])["meta"]
    tall = next(b for b in area["buildings"] if (b.get("levels") or 1) >= 2)
    sv = http("PUT", f"{base}/api/buildings/{tall['id']}", {"area_id": aid, "levels": 4, "levels_confirmed": True,
                                                              "structure": "beton", "access": "24jam",
                                                              "condition": "baik"}, token=tok)
    p = http("GET", f"{base}/api/area/patch{q}&since={meta['rev']}&epoch={meta['epoch']}")
    full_after = http("GET", base + "/api/area" + q)
    out["survey"] = {"put_s": round(sv[0], 3), "patch_s": round(p[0], 3), "patch_gzip_B": p[1],
                     "full_reload_gzip_B": full_after[1], "full_reload_s": round(full_after[0], 3)}
    return out


def run_size(n: int, profiles: list, port: int) -> dict:
    data = Path(tempfile.mkdtemp(prefix=f"sempat-perf-{n}-"))
    proc = None
    try:
        t0 = time.perf_counter()
        area = write_area(data, n)
        built = time.perf_counter() - t0
        env = dict(os.environ, SEMPAT_DATA_DIR=str(data), SEMPAT_BMKG_POLL="0", SEMPAT_FAKE_OSM="1")
        t_start = time.perf_counter()
        proc = subprocess.Popen([sys.executable, "run.py", "--no-browser", "--port", str(port)], cwd=ROOT, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://localhost:{port}"
        for _ in range(240):
            try:
                urllib.request.urlopen(base + "/api/meta", timeout=2)
                break
            except Exception:
                time.sleep(0.5)
        aid = area["meta"]["id"]
        t0 = t_start
        while True:  # proses server mulai -> area dimuat -> tabel rute pertama selesai dihitung + menghitung tabel rute pertama (di latar server)
            tb = json.loads(http("GET", base + "/api/tables?area=" + aid)[2])
            if tb.get("tables"):
                break
            if time.perf_counter() - t0 > 900:
                raise RuntimeError("tabel rute tidak selesai dihitung")
            time.sleep(0.2)
        load_s = time.perf_counter() - t0
        res = {"buildings": len(area["buildings"]), "nodes": len(area["nodes"]["lat"]), "build_area_s": round(built, 2),
               "server_ready_s": round(load_s, 2), "server": server_metrics(base, aid, area), "browser": {}}
        lat, lon = area["meta"]["center"][1], area["meta"]["center"][0]
        for prof in profiles:
            r = subprocess.run(["node", "tests/perf/perf.js"], cwd=ROOT, capture_output=True, text=True, timeout=1800,
                               env=dict(os.environ, BASE=base, AREA=aid, LAT=str(lat), LON=str(lon), PROFILE=prof))
            line = next((x for x in reversed(r.stdout.splitlines()) if x.startswith("{")), None)
            res["browser"][prof] = json.loads(line) if line else {"error": (r.stderr or r.stdout)[-800:]}
        return res
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(data, ignore_errors=True)


def fmt(rows: list) -> str:
    kb = lambda b: f"{b / 1024:,.0f} KB".replace(",", ".")  # noqa: E731
    out = ["## Server (lokal, tanpa throttling)", "",
           "| bangunan | server siap (proses mulai sampai tabel rute ada) | /api/area warga (dingin/hangat, gzip) | /api/area petugas (dingin/hangat, gzip) | "
           "tabel HP | grid waktu (dingin/cache) | survei: tambalan vs muat ulang |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        s = r["server"]
        out.append(f"| {r['buildings']} | {r['server_ready_s']} s | {s['area_publik']['cold_s']}/{s['area_publik']['warm_s']} s, "
                   f"{kb(s['area_publik']['gzip_B'])} | {s['area_petugas']['cold_s']}/{s['area_petugas']['warm_s']} s, "
                   f"{kb(s['area_petugas']['gzip_B'])} | {s['tables']['s']} s, {kb(s['tables']['gzip_B'])} | "
                   f"{s['time_grid']['cold_s']}/{s['time_grid']['cached_s']} s | {s['survey']['patch_gzip_B']} B vs "
                   f"{kb(s['survey']['full_reload_gzip_B'])} |")
    for prof in sorted({p for r in rows for p in r["browser"]}):
        out += ["", f"## Peramban: profil {prof}", "",
                "| bangunan | peran | area terunduh | peta siap | GeoJSON gedung | heap JS | bingkai/detik geser 3D (GPU perangkat lunak, indikatif) |",
                "|---|---|---|---|---|---|---|"]
        for r in rows:
            b = r["browser"].get(prof, {})
            if "error" in b:
                out.append(f"| {r['buildings']} | galat | {b['error'][:120]} | | | | |")
                continue
            for role, m in b.get("roles", {}).items():
                if "error" in m:
                    out.append(f"| {r['buildings']} | {role} | galat: {m['error'][:100]} | | | | |")
                    continue
                out.append(f"| {r['buildings']} | {role} | {m['t_area_ms']} ms | {m['t_ready_ms']} ms | "
                           f"{m['geojson_ms']} ms | {m['heap_MB']} MB | {m.get('fps_3d', '-')} |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="400,5000,20000")
    ap.add_argument("--profiles", default="desktop,android")
    ap.add_argument("--port", type=int, default=8781)
    ap.add_argument("--json")
    a = ap.parse_args()
    rows = [run_size(int(x), a.profiles.split(","), a.port) for x in a.sizes.split(",")]
    print(fmt(rows))
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
