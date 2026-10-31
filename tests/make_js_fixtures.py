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
eng = Engine(area)
pts = [dict(p, id=f"p{i}", updated_at="x") for i, p in enumerate(synthetic.seed_points())]
bundle = eng.live_tables(closures=set(), shelter_ids=[], points=pts, alert_active=True, wave_m=3.0)
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
(out / "area.json").write_text(json.dumps(area))
(out / "tables.json").write_text(json.dumps(bundle))
(out / "cases.json").write_text(json.dumps(cases))
print(f"{len(cases)} kasus ditulis ke {out}")
