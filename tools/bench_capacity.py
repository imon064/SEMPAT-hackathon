"""Benchmark & perbandingan alokasi kapasitas pada area grid SINTETIS (bukan data nyata).

Mengukur, untuk skenario yang sama persis:
  * new  : alokasi LP leksikografis (backend/allocation.py) seperti yang dipakai tabel HP + overrides;
  * base : tanpa kapasitas (setiap orang ke tujuan tercepat) — tabel tanpa offset;
  * old  : implementasi lama (penalti "lelang"); butuh salinan kode lama: --old-repo PATH.

Hasil "seperti HP": setiap bangunan berpenghuni disimulasikan memakai tabel yang dikirim ke HP (dan overrides untuk
`new`), jadi angka di bawah adalah yang benar-benar akan dialami rute warga, bukan angka internal solver.

    python tools/bench_capacity.py perf                 # matriks kinerja (kecil/sedang/besar, dengan/tanpa zona aman)
    python tools/bench_capacity.py compare --old-repo ../sempat-lama   # perbandingan 3 metode
    python tools/bench_capacity.py alert [--old-repo ...] # latensi peringatan (SSE/push) vs durasi optimasi
    Tambahkan --json out.json untuk menyimpan hasil mentah.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- area sintetis
def make_grid_area(n=140, houses=5000, shelters=150, safe_rows=2, cap_area=(40, 140), seed=3, area_id="bench"):
    """Grid n×n simpul (jarak 50 m). Baris paling atas = zona aman (bila safe_rows > 0). Rumah 4 orang di 60% bawah.

    SINTETIS: tidak mewakili kota mana pun. Kapasitas gedung dihitung engine dari luas tapak (rumus FEMA P-646).
    """
    from backend import area_builder as AB
    from backend.geo import LocalProjection
    rng = random.Random(seed)
    proj = LocalProjection(-8.0, 110.0)
    N = {"lat": [], "lon": [], "elev": [], "hazard": [], "safe": [], "coast": []}
    for j in range(n):
        for i in range(n):
            la, lo = proj.to_latlon(i * 50.0, j * 50.0)
            s = int(safe_rows > 0 and j >= n - safe_rows)
            N["lat"].append(round(la, 7)); N["lon"].append(round(lo, 7)); N["elev"].append(1.0 if not s else 30.0)
            N["hazard"].append(1 - s); N["safe"].append(s); N["coast"].append(50.0 + j * 50.0)
    E = {"u": [], "v": [], "len": [], "kind": [], "flag": []}
    for j in range(n):
        for i in range(n):
            a = j * n + i
            for b in ([a + 1] if i + 1 < n else []) + ([a + n] if j + 1 < n else []):
                E["u"].append(a); E["v"].append(b); E["len"].append(50.0); E["kind"].append("road"); E["flag"].append("")
    low = [e for e in range(len(E["u"])) if E["u"][e] // n < int(n * 0.6)]
    B = []

    def bld(e, **kw):
        u, v = E["u"][e], E["v"][e]
        la = (N["lat"][u] + N["lat"][v]) / 2 + 0.00005
        lo = (N["lon"][u] + N["lon"][v]) / 2
        d = 0.00004
        b = {"id": kw.pop("id"), "name": "", "kind": "rumah", "levels": 1, "levels_src": "default",
             "structure": "tidak_tahu", "area_m2": 80.0, "lat": la, "lon": lo,
             "poly": [[lo - d, la - d], [lo + d, la - d], [lo + d, la + d], [lo - d, la + d]],
             "edge": e, "t": 0.5, "perp_m": 6.0, "elev": 1.0, "elev_dem": 1.0, "elev_conf": "sedang",
             "residents": 0.0, "hazard": 1}
        b.update(kw)
        B.append(b)

    for k in range(houses):
        bld(rng.choice(low), id=f"h{k}", residents=4.0)
    for k in range(shelters):
        # Dianggap sudah disurvei (beton, akses 24 jam) agar bisa didaftarkan sebagai shelter resmi — SINTETIS.
        bld(rng.choice(low), id=f"s{k}", name=f"Sekolah {k}", kind="sekolah", levels=4, levels_src="survey",
            verified=True, structure="beton", access="24jam", area_m2=float(rng.randint(*cap_area)))
    lat0, lon0 = proj.to_latlon(n * 25.0, n * 25.0)
    return {"meta": {"id": area_id, "name": "Grid sintetis (benchmark)", "schema": AB.AREA_SCHEMA, "synthetic": True,
                     "created": "2026-11-01T00:00:00+00:00", "center": [lon0, lat0],
                     "bbox": [min(N["lon"]), min(N["lat"]), max(N["lon"]), max(N["lat"])],
                     "sources": ["SINTETIS"], "hazard_method": "rule",
                     "counts": {"nodes": n * n, "edges": len(E["u"]), "buildings": len(B)}},
            "params": dict(AB.DEFAULT_PARAMS), "nodes": N, "edges": E, "buildings": B,
            "coastline": [], "water": [], "official_points": [], "display": {}}


def _largest_remainder(vals):
    total = int(round(sum(vals)))
    base = [int(math.floor(v)) for v in vals]
    order = sorted(range(len(vals)), key=lambda i: (-(vals[i] - base[i]), i))
    for i in order[:max(0, total - sum(base))]:
        base[i] += 1
    return base


def demand_of(eng):
    """Orang bulat per bangunan & kelompok — sama dengan Engine.demand() baru; dipakai untuk SEMUA metode (kode lama
    tidak punya pembulatan ini) agar perbandingan memakai permintaan yang identik."""
    res = [max(0.0, float(b.get("residents") or 0)) for b in eng.buildings]
    tot = _largest_remainder(res)
    share = float(eng.params.get("vulnerable_share", 0.15))
    ren = _largest_remainder([t * share for t in tot])
    return [(bi, {"umum": t - min(r, t), "rentan": min(r, t)}) for bi, (t, r) in enumerate(zip(tot, ren)) if t > 0]


# --------------------------------------------------------------------------- simulasi HP
def phone_outcome(eng, lt, mode, budget_s, demand):
    """Ikuti tabel HP (+ overrides bila ada) dari setiap bangunan berpenghuni -> beban & waktu yang dialami warga."""
    dests = lt["dests"][mode]
    caps = [int(d.get("capacity") or 0) if d.get("id") else None for d in dests]
    nd = len([c for c in caps if c is not None])
    load = [0] * len(dests)
    out = {"to_shelter": 0, "to_unlimited": 0, "no_route": 0, "unresolved_override": 0, "late": 0,
           "time_sum": 0.0, "time_max": 0.0, "people": 0}
    ovr = lt.get("overrides") or {}
    for g in ("umum", "rentan"):
        tab = lt["tables"][f"{mode}-{g}"]
        tt, nx, ds = tab["time"], tab["next"], tab["dest"]
        by_edge = {}
        for r in ovr.get(f"{mode}-{g}", []):
            by_edge.setdefault(r["e"], []).append(r)
        counted = set()
        for bi, q in demand:
            n = q[g]
            if n <= 0:
                continue
            b = eng.buildings[bi]
            out["people"] += n
            rec, bd = None, None
            L = eng.elen[b["edge"]]
            for r in by_edge.get(b["edge"], []):
                dd = abs(r["t"] - b["t"]) * L
                if dd <= 25.0 and (bd is None or dd < bd):
                    rec, bd = r, dd
            if rec is not None:
                out["people"] -= n
                if id(rec) in counted:
                    continue
                counted.add(id(rec))
                out["people"] += sum(o["n"] for o in rec["opts"]) + rec["unres"]
                out["unresolved_override"] += rec["unres"]
                for o in rec["opts"]:
                    _add(out, load, o["d"], o["n"], o["s"], budget_s)
                continue
            cu, cv = eng._end_costs(b, g)
            u, v = eng.eu[b["edge"]], eng.ev[b["edge"]]
            ends = [(c + tt[x], x) for c, x in ((cu, u), (cv, v)) if tt[x] >= 0]
            if not ends:
                out["no_route"] += n
                continue
            tot, x = min(ends)
            while nx[x] != -1:
                x = nx[x]
            d = ds[x]
            if 0 <= d < nd:
                tot -= float(dests[d].get("pen_s") or 0.0)
                _add(out, load, d, n, tot, budget_s)
            else:
                _add(out, load, -1, n, tot, budget_s)
    over = [(dests[i]["id"], load[i], caps[i]) for i in range(nd) if load[i] > caps[i]]
    out["capacity_total"] = sum(caps[:nd])
    out["capacity_used"] = sum(min(load[i], caps[i]) for i in range(nd))
    out["unused_capacity"] = sum(max(0, caps[i] - load[i]) for i in range(nd))
    out["violations"] = len(over)
    out["excess_people"] = sum(l - c for _, l, c in over)
    out["max_over_ratio"] = round(max([l / c if c else float("inf") for _, l, c in over] + [0.0]), 2)
    out["time_mean"] = out["time_sum"] / max(1, out["to_shelter"] + out["to_unlimited"])
    out["load"] = load[:nd]
    return out


def _add(out, load, d, n, t, budget):
    if d >= 0:
        load[d] += n
        out["to_shelter"] += n
    else:
        out["to_unlimited"] += n
    out["time_sum"] += n * t
    out["time_max"] = max(out["time_max"], t)
    if budget is not None and t > budget + 1e-9:
        out["late"] += n


# --------------------------------------------------------------------------- pekerja (proses terpisah per repo)
def worker(args):
    sys.path.insert(0, args.repo)
    os.chdir(args.repo)
    from backend.engine import Engine
    sc = json.loads(args.scenario)
    area = make_grid_area(**sc["area"])
    if "prioritize_deadline" in sc:  # kebijakan tahap batas waktu (parameter area)
        area["params"]["alloc_prioritize_deadline"] = bool(sc["prioritize_deadline"])
    t0 = time.perf_counter()
    eng = Engine(area)
    t_load = time.perf_counter() - t0
    sids = [b["id"] for b in eng.buildings if b["id"].startswith("s")] if sc["mode"] == "aman" else []
    H = 3.0
    eta = sc.get("eta_min", 20.0)
    budget = max(0.0, (eta - 5.0 - 2.0) * 60.0)
    demand = demand_of(eng)
    res = {"kind": args.kind, "scenario": sc, "nodes": eng.n, "engine_load_s": round(t_load, 2),
           "people": sum(sum(q.values()) for _, q in demand)}
    t0 = time.perf_counter()
    if args.kind == "old":
        lt = eng.live_tables(closures=set(), shelter_ids=sids, points=[], alert_active=True, wave_m=H)
    else:
        lt = eng.live_tables(closures=set(), shelter_ids=sids, points=[], alert_active=True, wave_m=H, eta_min=eta,
                             alloc="off" if args.kind == "base" else "sync")
        lt.pop("_alloc_requests", None)
    res["tables_s"] = round(time.perf_counter() - t0, 2)
    mode = sc["mode"]
    res["n_dests"] = len([d for d in lt["dests"][mode] if d.get("id")])
    res["phone"] = phone_outcome(eng, lt, mode, budget, demand)
    blob = json.dumps(lt, separators=(",", ":")).encode()
    res["tables_kb"] = round(len(blob) / 1024)
    res["tables_gzip_kb"] = round(len(gzip.compress(blob)) / 1024)
    if args.kind == "new":
        cap = lt["capacity"][mode]
        res["status"] = cap["status"]
        res["timing"] = cap.get("timing")
        res["alloc_totals"] = cap.get("totals")
        res["n_overrides"] = cap.get("overrides")
        ov = json.dumps(lt["overrides"], separators=(",", ":")).encode()
        res["overrides_kb"] = round(len(ov) / 1024)
        res["overrides_gzip_kb"] = round(len(gzip.compress(ov)) / 1024)
        dash = [d["allocated"] for d in cap.get("dests", [])] if cap.get("totals") else None
        res["dashboard_eq_phone"] = dash == res["phone"]["load"] if dash is not None else None
        res["errors"] = cap.get("errors")
        res["optimal"] = cap.get("optimal")
        res["method"] = cap.get("method")
    elif args.kind == "old":
        cc = (lt.get("capacity_check") or {}).get(mode) or {}
        dl = cc.get("load")
        if isinstance(dl, dict):
            ids = [d["id"] for d in lt["dests"][mode] if d.get("id")]
            dl = [round(dl.get(i, 0)) for i in ids]
        res["dashboard_load"] = dl
        res["dashboard_overflow"] = cc.get("overflow")
        res["dashboard_eq_phone"] = (dl == res["phone"]["load"]) if dl is not None else None
    res["phone"].pop("load")
    print("@@" + json.dumps(res))


def run_worker(kind, repo, scenario, timeout=3600):
    cmd = [sys.executable, str(Path(__file__).resolve()), "_worker", "--kind", kind, "--repo", str(repo),
           "--scenario", json.dumps(scenario)]
    env = dict(os.environ, PYTHONPATH=str(repo))
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    line = next((x for x in p.stdout.splitlines() if x.startswith("@@")), None)
    if line is None:
        raise RuntimeError(f"pekerja {kind} gagal:\n{p.stdout[-2000:]}\n{p.stderr[-3000:]}")
    return json.loads(line[2:])


# --------------------------------------------------------------------------- skenario
PERF = [
    ("kecil", dict(n=40, houses=400, shelters=15, cap_area=(40, 140))),
    ("sedang", dict(n=80, houses=1800, shelters=60, cap_area=(40, 140))),
    ("besar", dict(n=140, houses=5000, shelters=150, cap_area=(40, 140))),
    ("besar-penduduk2x", dict(n=140, houses=10000, shelters=150, cap_area=(40, 140))),
    ("besar-terburuk", dict(n=140, houses=10000, shelters=150, cap_area=(10, 30))),
]


def cmd_perf(a):
    rows = []
    for name, ar in PERF:
        for safe in (2, 0):
            sc = {"area": dict(ar, safe_rows=safe), "mode": "aman", "eta_min": 20.0}
            r = run_worker("new", ROOT, sc)
            r["name"] = f"{name}{'' if safe else ' (tanpa zona aman)'}"
            rows.append(r)
            t = r["timing"] or {}
            ph = r["phone"]
            print(f"{r['name']:34s} simpul {r['nodes']:6d} tujuan {r['n_dests']:4d} orang {r['people']:6d} | "
                  f"kandidat {t.get('candidates_s', 0):5.2f} solver {t.get('solve_s', 0):5.2f} harga {t.get('price_s', 0):5.2f} "
                  f"rekon {t.get('reconcile_s', 0):5.3f} validasi {t.get('validate_s', 0):5.3f} enkode {t.get('encode_s', 0):5.2f} "
                  f"total {t.get('all_s', 0):6.2f}s | status {r['status']} override {r['n_overrides']} "
                  f"({r['overrides_gzip_kb']} KB gz, tabel {r['tables_gzip_kb']} KB gz) | HP: gedung {ph['to_shelter']} "
                  f"aman {ph['to_unlimited']} tanpa {ph['no_route'] + ph['unresolved_override']} langgar {ph['violations']} "
                  f"dasbor=HP {r['dashboard_eq_phone']}", flush=True)
    return rows


COMPARE = [
    ("audit-besar", {"area": dict(n=140, houses=5000, shelters=150, cap_area=(40, 140), safe_rows=2), "mode": "aman"}),
    ("audit-besar tanpa zona aman", {"area": dict(n=140, houses=5000, shelters=150, cap_area=(40, 140), safe_rows=0),
                                     "mode": "aman"}),
    ("sedang", {"area": dict(n=80, houses=1800, shelters=60, cap_area=(40, 140), safe_rows=2), "mode": "aman"}),
    ("sedang kapasitas langka", {"area": dict(n=80, houses=3000, shelters=60, cap_area=(10, 30), safe_rows=2),
                                 "mode": "aman"}),
]
# Variasi kebijakan: tahap batas waktu dimatikan (hanya metode new; lihat DESIGN.md §2).
POLICY = [
    ("audit-besar, tanpa tahap batas waktu", {"area": dict(n=140, houses=5000, shelters=150, cap_area=(40, 140),
                                                           safe_rows=2), "mode": "aman", "prioritize_deadline": False}),
    ("sedang, tanpa tahap batas waktu", {"area": dict(n=80, houses=1800, shelters=60, cap_area=(40, 140), safe_rows=2),
                                         "mode": "aman", "prioritize_deadline": False}),
]


def cmd_compare(a):
    rows = []
    for name, sc in COMPARE + (POLICY if a.policy else []):
        sc = dict(sc, eta_min=20.0)
        kinds = [("base", ROOT), ("new", ROOT)] + ([("old", Path(a.old_repo))] if a.old_repo else [])
        if "prioritize_deadline" in sc:
            kinds = [("new", ROOT)]
        for kind, repo in kinds:
            r = run_worker(kind, repo, sc)
            r["name"] = name
            rows.append(r)
            ph = r["phone"]
            print(f"{name:28s} {kind:4s} | langgar {ph['violations']:3d} (+{ph['excess_people']:5d} org, maks x{ph['max_over_ratio']}) "
                  f"gedung {ph['to_shelter']:6d} zona-aman {ph['to_unlimited']:6d} tanpa-tujuan {ph['no_route'] + ph['unresolved_override']:5d} "
                  f"terlambat {ph['late']:6d} | waktu total {ph['time_sum'] / 3600:8.1f} jam rata2 {ph['time_mean'] / 60:5.1f} mnt "
                  f"maks {ph['time_max'] / 60:6.1f} mnt | kapasitas tak terpakai {ph['unused_capacity']:6d} | "
                  f"tabel {r['tables_s']:6.1f}s | dasbor=HP {r.get('dashboard_eq_phone')}", flush=True)
    return rows


ALERT_SNIPPET = r"""
import json, os, sys, tempfile, time, shutil
sys.path.insert(0, os.environ["REPO"]); sys.path.insert(0, os.environ["TOOLS"])
from pathlib import Path
from bench_capacity import make_grid_area
from backend import config
tmp = Path(tempfile.mkdtemp())
config.DATA_DIR = tmp; config.CACHE_DIR = tmp / "cache"; config.BMKG_POLL = False
config.USERS_PATH = tmp / "users.json"; config.SECRET_PATH = tmp / "secret.key"
from backend import app as A
area = make_grid_area(n=int(os.environ["N"]), houses=int(os.environ["HOUSES"]), shelters=150)
p = tmp / "areas" / "bench" / "area.json"; p.parent.mkdir(parents=True); p.write_text(json.dumps(area))
st = A.AppState(tmp / "areas", tmp / "t.db")
c = st.get("bench")
for b in c.engine.buildings:
    if b["id"].startswith("s"):
        st.store.register_shelter("bench", b["id"], "bench")
st.recompute("bench", "awal")
if hasattr(st, "wait_allocations"):
    st.wait_allocations(900)
marks = {}
orig = st.broker.publish
def pub(ev, data):
    marks.setdefault(ev + ":" + str(data.get("reason", "")), time.perf_counter())
    return orig(ev, data)
st.broker.publish = pub
st.push_broadcast = lambda *a, **k: marks.setdefault("push", time.perf_counter())
t0 = time.perf_counter()
A.create_alert(st, {"source": "SIMULASI", "event": "Tsunami", "magnitude": 7.5, "depth_km": 10, "lat": -8.5,
                    "lon": 110.0, "region": "uji", "tsunami": 1, "eta_min": 20, "headline": "UJI", "instruction": "uji",
                    "radius_km": None, "level": "siaga"})
t1 = time.perf_counter()
out = {"create_alert_s": t1 - t0, "sse_alert_s": marks["alert:"] - t0, "push_s": marks["push"] - t0}
if hasattr(st, "wait_allocations"):
    st.wait_allocations(900)
    out["allocation_done_s"] = time.perf_counter() - t0
    tb = json.loads(st.get("bench").tables_bytes)
    out["capacity_status"] = {m: tb["capacity"][m]["status"] for m in tb["capacity"]}
shutil.rmtree(tmp, ignore_errors=True)
print("@@" + json.dumps(out))
"""


def cmd_alert(a):
    rows = []
    for kind, repo in [("new", ROOT)] + ([("old", Path(a.old_repo))] if a.old_repo else []):
        for n, houses in ((80, 1800), (140, 5000)):
            env = dict(os.environ, REPO=str(repo), TOOLS=str(Path(__file__).resolve().parent), N=str(n),
                       HOUSES=str(houses), PYTHONPATH=str(repo))
            p = subprocess.run([sys.executable, "-c", ALERT_SNIPPET], capture_output=True, text=True, env=env,
                               timeout=3600, cwd=str(repo))
            line = next((x for x in p.stdout.splitlines() if x.startswith("@@")), None)
            if line is None:
                raise RuntimeError(p.stdout[-2000:] + p.stderr[-3000:])
            r = dict(json.loads(line[2:]), kind=kind, nodes=n * n, houses=houses)
            rows.append(r)
            print(f"{kind:4s} simpul {n * n:6d}: SSE peringatan +{r['sse_alert_s']:.3f}s, push +{r['push_s']:.3f}s, "
                  f"create_alert {r['create_alert_s']:.2f}s"
                  + (f", alokasi selesai +{r['allocation_done_s']:.1f}s {r['capacity_status']}" if "allocation_done_s" in r else ""),
                  flush=True)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["perf", "compare", "alert", "_worker"])
    ap.add_argument("--old-repo")
    ap.add_argument("--json")
    ap.add_argument("--policy", action="store_true", help="compare: juga jalankan variasi tanpa tahap batas waktu")
    ap.add_argument("--kind")
    ap.add_argument("--repo")
    ap.add_argument("--scenario")
    a = ap.parse_args()
    if a.cmd == "_worker":
        return worker(a)
    rows = {"perf": cmd_perf, "compare": cmd_compare, "alert": cmd_alert}[a.cmd](a)
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
