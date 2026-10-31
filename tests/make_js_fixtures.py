"""Buat data uji untuk tests/js/core.test.mjs (membandingkan rute klien vs server)."""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import synthetic  # noqa: E402
from backend.engine import Engine  # noqa: E402

out = Path(__file__).parent / "js"
area = synthetic.build(log=lambda *_: None)
# Gedung bertingkat dibuat TERVERIFIKASI (survei lengkap + elevasi terukur) agar ada tujuan gedung & kapasitas:
# sejak kebijakan 1 Nov 2026 gedung Perlu Verifikasi bukan tujuan rute/alokasi. FIXTURE uji, bukan data nyata.
for b in area["buildings"]:
    if b["kind"] != "rumah" and int(b.get("levels") or 1) >= 2:
        b.update(levels_src="survey", surveyed=True, condition="baik", structure="beton", access="24jam",
                 elev_src="survei", elev_method="benchmark", levels_est=False)
eng = Engine(area)
pts = [dict(p, id=f"p{i}", updated_at="x") for i, p in enumerate(synthetic.seed_points())]
bundle = eng.live_tables(closures=set(), shelter_ids=[], points=pts, alert_active=True, wave_m=3.0)
bundle.pop("_alloc_requests", None)
rng = random.Random(5)
cases = []
for _ in range(40):
    x, y = rng.uniform(150, 2300), rng.uniform(200, 1150)
    lat, lon = synthetic.PROJ.to_latlon(x, y)
    for group in ("umum", "rentan"):
        r = eng.route(lat, lon, mode="aman", group=group, closures=set(), shelter_ids=[], points=pts,
                      alert_active=False)
        if r["ok"] and r["routes"]:
            rt = r["routes"][0]
            cases.append({"lat": lat, "lon": lon, "mode": "aman", "group": group,
                          "server_time_s": rt["time_s"], "server_dest_type": rt["dest"]["type"]})
        # Mode gedung (pilihan darurat, tsunami 3 m): tabel = tercepat dari zona aman, gedung layak, tanah tinggi.
        g = eng.route(lat, lon, mode="gedung", group=group, closures=set(), shelter_ids=[], points=pts,
                      alert_active=True, wave_m=3.0, alternatives=1)
        best = []
        if r["ok"] and r["routes"]:
            best.append((r["routes"][0]["time_s"], r["routes"][0]["dest"]["type"]))
        if g["ok"] and g["routes"]:
            best.append((g["routes"][0]["time_s"], g["routes"][0]["dest"]["type"]))
        if best and not r.get("already_safe"):
            t, typ = min(best)
            cases.append({"lat": lat, "lon": lon, "mode": "gedung", "group": group,
                          "server_time_s": t, "server_dest_type": typ})
# Kasus di sekitar gedung yang terkena penalti kapasitas: rute klien (tabel) & server harus tetap sama persis.
crowded = [d for d in bundle["dests"]["gedung"] if d.get("pen_s")]
for d in crowded:
    for dx, dy in ((0, 0), (40, 0), (-40, 25), (0, -60), (80, 40)):
        x, y = synthetic.PROJ.to_xy(d["lat"], d["lon"])
        lat, lon = synthetic.PROJ.to_latlon(x + dx, y + dy)
        for group in ("umum", "rentan"):
            g = eng.route(lat, lon, mode="gedung", group=group, closures=set(), shelter_ids=[], points=pts,
                          alert_active=True, wave_m=3.0, alternatives=1)
            r = eng.route(lat, lon, mode="aman", group=group, closures=set(), shelter_ids=[], points=pts,
                          alert_active=False)
            if r.get("already_safe") or not (g["ok"] and g["routes"]):
                continue
            best = [(g["routes"][0]["time_s"], g["routes"][0]["dest"]["type"], g["routes"][0]["dest"].get("id"))]
            if r["ok"] and r["routes"]:
                best.append((r["routes"][0]["time_s"], r["routes"][0]["dest"]["type"], None))
            t, typ, did = min(best, key=lambda x: x[0])
            cases.append({"lat": lat, "lon": lon, "mode": "gedung", "group": group, "server_time_s": t,
                          "server_dest_type": typ, "server_dest_id": did})
# ---- Alokasi kapasitas: overrides (bangunan yang alokasinya dipecah / tak terwakili tabel) & pickOption ----
from backend import allocation as AL  # noqa: E402


def override_cases(bundle, shelter_ids, occupancy):
    """Posisi tepat di titik asal tiap override: rute HP (tabel+overrides+device key) harus = route() server."""
    out = []
    for key, recs in bundle["overrides"].items():
        mode, group = key.split("-")
        for rec in recs:
            e, t = rec["e"], rec["t"]
            u, v = eng.eu[e], eng.ev[e]
            lat = eng.lat[u] + t * (eng.lat[v] - eng.lat[u])
            lon = eng.lon[u] + t * (eng.lon[v] - eng.lon[u])
            s = eng.snap(lat, lon)
            if s["edge"] != e:
                continue  # posisi di simpul bersama: ruas lain bisa terpilih; bukan kasus override
            for dk in ("hp-a", "hp-b", "hp-c", "7f3a9c"):
                r = eng.route(lat, lon, mode=mode, group=group, closures=set(), shelter_ids=shelter_ids, points=pts,
                              alert_active=True, wave_m=3.0, alternatives=1, device_key=dk, occupancy=occupancy)
                if not (r["ok"] and r.get("routes")):
                    continue
                rt = r["routes"][0]
                out.append({"lat": lat, "lon": lon, "mode": mode, "group": group, "device_key": dk,
                            "origin": r["allocation"]["origin"], "option": r["allocation"]["option"], "server_time_s": rt["time_s"],
                            "server_dest_type": rt["dest"]["type"], "server_dest_id": rt["dest"].get("id"),
                            "server_n_coords": len(rt["coords"])})
    return out


ov_cases = override_cases(bundle, [], None)
# Skenario 2: shelter resmi dengan sebagian kapasitas sudah terisi (okupansi petugas) -> kapasitas mengikat.
sids = [d["id"] for d in bundle["dests"]["gedung"] if d.get("id")][:4]
occ = {}
for d in bundle["dests"]["gedung"]:
    if d.get("id") in sids:
        occ[d["id"]] = max(0, int(d["capacity"]) - 15)
bundle2 = eng.live_tables(closures=set(), shelter_ids=sids, points=pts, alert_active=True, wave_m=3.0, occupancy=occ)
bundle2.pop("_alloc_requests", None)
ov_cases2 = override_cases(bundle2, sids, occ)
rng2 = random.Random(11)
pick = []
for _ in range(300):
    key = "".join(rng2.choice("0123456789abcdef") for _ in range(rng2.choice((4, 8, 16))))
    origin = rng2.choice(["b12", "w/123456789", "n-77", "gedung é", f"b{rng2.randint(0, 99999)}"])
    counts = [rng2.randint(0, 40) for _ in range(rng2.randint(1, 5))]
    pick.append({"key": key, "origin": origin, "counts": counts, "expect": AL.pick_option(key, origin, counts)})
# ---- Batas waktu: HP (core.js isLate pada detik ke-start_delay) == alokasi (waktu tempuh > budget_s) ----
pol = bundle["deadline"]
deadline_cases = []
for eta in (8, 15, 20, 30):
    budget = eng.budget_s(eta)
    for route_s in range(0, int(eta * 60) + 240, 37):
        deadline_cases.append({"eta_min": eta, "route_s": route_s, "late_alloc": route_s > budget,
                               "elapsed_s": pol["start_delay_s"]})
(out / "deadline_cases.json").write_text(json.dumps({"policy": pol, "cases": deadline_cases}))
(out / "tables2.json").write_text(json.dumps(bundle2))
(out / "alloc_cases.json").write_text(json.dumps({"bundle1": ov_cases, "bundle2": ov_cases2, "pick": pick}))
print(f"override: {len(ov_cases)} + {len(ov_cases2)} kasus; pickOption: {len(pick)} vektor")

(out / "area.json").write_text(json.dumps(area))
(out / "tables.json").write_text(json.dumps(bundle))
(out / "cases.json").write_text(json.dumps(cases))
print(f"{len(cases)} kasus ditulis ke {out}")
