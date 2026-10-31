"""Uji model tsunami: lantai aman, kelayakan gedung, ruas berisiko, sumber tinggi gedung, survei, peta bahaya."""
import json
import sys
import types
import unittest

from backend import area_builder, heights, synthetic, tsunami
from backend.engine import Engine

from tests.test_api import APITestBase


def _b(**kw):
    # Elevasi DIUKUR (margin 0 m) agar uji aturan lantai di sini tidak bercampur dengan margin DEM (diuji di
    # tests/test_milestone1.py).
    b = {"id": "x", "kind": "sekolah", "levels": 4, "levels_src": "osm", "elev": 1.0, "area_m2": 400.0,
         "structure": "tidak_tahu", "elev_src": "survei", "elev_method": "benchmark"}
    b.update(kw)
    return b


class TestRules(unittest.TestCase):
    def test_min_floor_japan_guideline(self):
        # Pedoman Kantor Kabinet Jepang 2005: genangan 2 m -> lantai 3, 3 m -> lantai 4.
        self.assertEqual(tsunami.min_safe_floor(2.0), 3)
        self.assertEqual(tsunami.min_safe_floor(3.0), 4)
        self.assertEqual(tsunami.min_safe_floor(0.4), 2)
        self.assertEqual(tsunami.min_safe_floor(0.0), 1)

    def test_wave_height_by_level(self):
        self.assertEqual(tsunami.wave_height_for("waspada", None, 10), 0.5)
        self.assertEqual(tsunami.wave_height_for("siaga", None, 10), 3.0)
        self.assertEqual(tsunami.wave_height_for("awas", None, 10), 10)
        self.assertEqual(tsunami.wave_height_for(None, None, 8), 8)
        self.assertEqual(tsunami.wave_height_for("awas", 6.5, 10), 6.5)  # perkiraan resmi diutamakan

    def test_assess(self):
        p = area_builder.DEFAULT_PARAMS
        a = tsunami.assess(_b(levels=4, elev=1.0), 3.0, p)  # genangan 2 m -> lantai 3
        self.assertTrue(a["suitable"])
        self.assertEqual(a["min_floor"], 3)
        self.assertEqual(a["usable_floors"], 2)
        self.assertEqual(a["capacity"], int(400 * 2 * 0.5 / 0.93))
        self.assertAlmostEqual(a["climb_s"], 2 * 4.0 / 0.21, places=0)  # satu tinggi lantai: height_per_level_m 4 m
        self.assertFalse(tsunami.assess(_b(levels=2, elev=1.0), 3.0, p)["suitable"])        # terlalu rendah
        self.assertFalse(tsunami.assess(_b(levels_src="default"), 3.0, p)["suitable"])      # lantai tak diketahui
        self.assertFalse(tsunami.assess(_b(kind="rumah"), 3.0, p)["suitable"])              # rumah pribadi
        self.assertFalse(tsunami.assess(_b(structure="ringan"), 3.0, p)["suitable"])        # kayu/bambu
        self.assertFalse(tsunami.assess(_b(access="tidak"), 3.0, p)["suitable"])
        self.assertFalse(tsunami.assess(_b(elev=12.0), 3.0, p)["suitable"])                 # tidak tergenang
        self.assertTrue(tsunami.assess(_b(kind="rumah", levels_src="survey"), 3.0, p)["suitable"])

    def test_structure_from_osm(self):
        self.assertEqual(tsunami.structure_from_tags({"building:material": "concrete"}), "beton")
        self.assertEqual(tsunami.structure_from_tags({"building:material": "wood"}), "ringan")
        self.assertEqual(tsunami.structure_from_tags({}), "tidak_tahu")


def _mini_overpass():
    """Jalan lurus dari pantai ke bukit + jalur alternatif lewat terowongan, satu jembatan, dua gedung."""
    def way(i, nodes, tags):
        return {"type": "way", "id": i, "nodes": [n for n, _ in nodes], "tags": tags,
                "geometry": [{"lat": la, "lon": lo} for _, (la, lo) in nodes]}
    lat0, lon0 = -8.0, 110.0
    d = 0.0009  # ± 100 m
    P = {k: (lat0 + y * d, lon0 + x * d) for k, (x, y) in {
        1: (0, 1), 2: (0, 3), 3: (0, 6), 4: (0, 10), 5: (2, 3), 6: (2, 6), 7: (-2, 3), 8: (-2, 6)}.items()}
    els = [
        way(1, [(1, P[1]), (2, P[2])], {"highway": "residential"}),
        way(2, [(2, P[2]), (3, P[3])], {"highway": "residential", "tunnel": "yes", "layer": "-1"}),
        way(3, [(3, P[3]), (4, P[4])], {"highway": "residential"}),
        way(4, [(2, P[2]), (5, P[5]), (6, P[6]), (3, P[3])], {"highway": "residential"}),
        way(5, [(2, P[2]), (7, P[7]), (8, P[8]), (3, P[3])], {"highway": "residential", "bridge": "yes"}),
        {"type": "way", "id": 50, "nodes": [], "tags": {"natural": "coastline"},
         "geometry": [{"lat": lat0, "lon": lon0 - 0.01}, {"lat": lat0, "lon": lon0 + 0.01}]},
    ]

    def bld(i, x, y, tags):
        la, lo = lat0 + y * d, lon0 + x * d
        r = 0.00012
        ring = [(la, lo), (la, lo + r), (la + r, lo + r), (la + r, lo), (la, lo)]
        return {"type": "way", "id": i, "nodes": list(range(900 + i * 10, 905 + i * 10)), "tags": tags,
                "geometry": [{"lat": a, "lon": b} for a, b in ring]}
    els.append(bld(60, 0.3, 2, {"building": "school", "building:levels": "5", "building:material": "concrete",
                                "building:levels:underground": "1", "name": "SMA Uji"}))
    els.append(bld(61, -0.3, 2, {"building": "commercial", "height": "13"}))
    els.append(bld(62, 0.3, 4, {"building": "commercial"}))

    def dem(points):  # datar 0,5 m sampai 300 m dari pantai, lalu naik 4 m per 100 m
        return [max(0.5, 0.5 + ((la - lat0) / d - 3.0) * 4.0) for la, lo in points]
    return {"elements": els}, dem


class TestBuilderAndEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data, dem = _mini_overpass()
        cls.area = area_builder.build_area(data, dem, area_id="mini", name="Mini", log=lambda *_: None)
        cls.eng = Engine(cls.area)

    def test_levels_sources_and_flags(self):
        B = {b["name"] or b["id"]: b for b in self.area["buildings"]}
        sma = B["SMA Uji"]
        self.assertEqual((sma["levels"], sma["levels_src"], sma["structure"], sma["basement"]), (5, "osm", "beton", 1))
        h = B["w61"]
        # height 13 m / 3,5 m per lantai (ASUMSI) = 3 lantai utuh, ditandai perkiraan
        self.assertEqual((h["levels"], h["levels_src"], h["height_m"], h["levels_est"]), (3, "osm_height", 13.0, True))
        self.assertEqual(B["w62"]["levels_src"], "default")
        flags = self.area["edges"]["flag"]
        self.assertIn("under", flags)
        self.assertIn("bridge", flags)

    def test_dem_margin(self):
        # Ambang 20 m + galat DEM 5 m: target aman baru mulai dari elevasi 25 m.
        N = self.area["nodes"]
        safe_elev = [e for e, s, c in zip(N["elev"], N["safe"], N["coast"]) if s and c < 3000]
        self.assertTrue(all(e >= 25 for e in safe_elev))

    def test_underpass_avoided(self):
        e_under = self.area["edges"]["flag"].index("under")
        u, v = self.area["edges"]["u"][e_under], self.area["edges"]["v"][e_under]
        self.assertTrue(self.area["nodes"]["hazard"][u] or self.area["nodes"]["hazard"][v])
        self.assertEqual(self.eng.edge_penalty(e_under), 10.0)
        lat, lon = self.area["nodes"]["lat"][u], self.area["nodes"]["lon"][u]
        r = self.eng.route(lat, lon, mode="aman", group="umum", closures=set(), shelter_ids=[], points=[],
                           alert_active=True)
        self.assertTrue(r["ok"])
        # Rute tidak melewati terowongan: titik tengahnya tidak berada di garis x = 0 antara node 2 dan 3.
        mids = [c for c in r["routes"][0]["coords"] if abs(c[0] - 110.0) > 0.0005]
        self.assertTrue(mids, "rute seharusnya memutar lewat jalan samping")

    def test_gedung_table_and_scenario(self):
        # SMA Uji belum disurvei (Perlu Verifikasi): BUKAN tujuan rute/alokasi, hanya informasi tanpa rute.
        lt0 = self.eng.live_tables(closures=set(), shelter_ids=[], points=[], alert_active=True, wave_m=3.0)
        self.assertNotIn("SMA Uji", {d["name"] for d in lt0["dests"]["gedung"]})
        b0 = next(x for x in self.area["buildings"] if x["name"] == "SMA Uji")
        a0 = self.eng.assess(b0, 3.0)
        self.assertFalse(a0["destination"])
        # daftar info = lolos lantai aman konservatif DAN belum terverifikasi (sama dengan assess)
        self.assertEqual("SMA Uji" in {d["name"] for d in lt0["dests"]["unverified"]}, a0["suitable"])
        # Setelah survei lengkap + elevasi terukur: tujuan gedung dengan lantai aman tanpa margin.
        area = json.loads(json.dumps(self.area))
        b = next(x for x in area["buildings"] if x["name"] == "SMA Uji")
        b.update(levels_src="survey", surveyed=True, condition="baik", access="24jam", elev_src="survei",
                 elev_method="benchmark")
        self.eng = Engine(area)
        self.area = area
        lt = self.eng.live_tables(closures=set(), shelter_ids=[], points=[], alert_active=True, wave_m=3.0)
        self.assertIn("gedung-umum", lt["tables"])
        names = {d["name"]: d for d in lt["dests"]["gedung"]}
        self.assertIn("SMA Uji", names)
        self.assertEqual(lt["scenario"]["wave_m"], 3.0)
        sma = names["SMA Uji"]
        self.assertEqual(sma["min_floor"], tsunami.min_safe_floor(3.0 - b["elev"]))
        # Gedung tanpa data lantai tidak pernah disarankan
        self.assertFalse(any(d.get("id") == "w62" for d in lt["dests"]["gedung"]))
        hg = lt["dests"]["gedung"][-1]
        self.assertEqual((hg["type"], hg["min_elev"]), ("tanah_tinggi", 8.0))  # 3 m + galat DEM 5 m
        # Simpul tanah tinggi darurat (>= 8 m, belum zona aman resmi) menjadi tujuan di tabel gedung
        N = self.area["nodes"]
        tab = lt["tables"]["gedung-umum"]
        hi = [i for i in range(len(N["elev"])) if N["elev"][i] >= 8 and not N["safe"][i]]
        self.assertTrue(hi)
        self.assertTrue(all(tab["time"][i] == 0 and tab["dest"][i] == len(lt["dests"]["gedung"]) - 1 for i in hi))
        # Skenario sangat tinggi: SMA 5 lantai tidak lagi cukup
        lt2 = self.eng.live_tables(closures=set(), shelter_ids=[], points=[], alert_active=True, wave_m=12.0)
        self.assertFalse(any(d["name"] == "SMA Uji" for d in lt2["dests"]["gedung"]))
        r = self.eng.route(self.area["nodes"]["lat"][0], self.area["nodes"]["lon"][0], mode="gedung", group="umum",
                           closures=set(), shelter_ids=[], points=[], alert_active=True, wave_m=3.0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["routes"][0]["dest"]["type"], "gedung")

    def test_upgrade_old_area(self):
        old = json.loads(json.dumps(self.area))
        old["meta"].pop("schema")
        old["edges"].pop("flag")
        for b in old["buildings"]:
            b.pop("levels_src")
        area_builder.upgrade_area(old)
        self.assertEqual(len(old["edges"]["flag"]), len(old["edges"]["u"]))
        Engine(old)


class TestPlanScenario(unittest.TestCase):
    def test_plan_design_height(self):
        area = synthetic.build(log=lambda *_: None)
        eng = Engine(area)
        lo = eng.plan(eta_min=20, delay_min=5, margin_min=2, shelter_ids=[], optimize_k=3, design_height_m=3.0)
        hi = eng.plan(eta_min=20, delay_min=5, margin_min=2, shelter_ids=[], optimize_k=3, design_height_m=10.0)
        self.assertEqual(lo["wave_m"], 3.0)
        names_lo = {b["name"] for b in lo["recommended"]}
        names_hi = {b["name"] for b in hi["recommended"]}
        # Masjid 2 lantai, elevasi DEM 2,1 m (kepercayaan sedang): nominal lantai 2 cukup untuk 3 m, tetapi lantai aman
        # KONSERVATIF (margin 3 m) = lantai 5 -> TIDAK layak (audit D-2). Dengan elevasi terukur: layak.
        mas = next(b for b in area["buildings"] if b["name"] == "Masjid Al-Ikhlas")
        a = eng.assess(mas, 3.0)
        self.assertEqual((a["min_floor_nominal"], a["min_floor"], a["suitable"]), (2, 5, False))
        self.assertNotIn(mas["id"], lo["suitable_ids"])
        mas.update(elev_src="survei", elev_method="benchmark")
        self.assertTrue(eng.assess(mas, 3.0)["suitable"])
        self.assertFalse(eng.assess(mas, 10.0)["suitable"])
        self.assertNotIn("Masjid Al-Ikhlas", names_hi)
        for r in lo["recommended"] + hi["recommended"]:
            self.assertGreaterEqual(r["levels"], r["min_floor"])
        self.assertTrue(names_lo or names_hi)


class TestHeights(unittest.TestCase):
    def setUp(self):
        data, dem = _mini_overpass()
        self.area = area_builder.build_area(data, dem, area_id="mini", name="Mini", log=lambda *_: None)
        self.eng = Engine(self.area)

    def _ring(self, b, grow=0.00002):
        xs = [p[0] for p in b["poly"]]
        ys = [p[1] for p in b["poly"]]
        return [[min(xs) - grow, min(ys) - grow], [max(xs) + grow, min(ys) - grow], [max(xs) + grow, max(ys) + grow],
                [min(xs) - grow, max(ys) + grow], [min(xs) - grow, min(ys) - grow]]

    def test_upload_matches_and_respects_rank(self):
        B = {b["id"]: b for b in self.area["buildings"]}
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"height": 16.4},
             "geometry": {"type": "Polygon", "coordinates": [self._ring(B["w62"])]}},
            {"type": "Feature", "properties": {"height": 3.0},  # SMA sudah ada data OSM -> tidak ditimpa
             "geometry": {"type": "Polygon", "coordinates": [self._ring(B["w60"])]}},
            {"type": "Feature", "properties": {"height": 21.0, "name": "Gedung baru"},
             "geometry": {"type": "Polygon", "coordinates": [[[110.0008, -7.9982], [110.0010, -7.9982],
                                                              [110.0010, -7.9980], [110.0008, -7.9980],
                                                              [110.0008, -7.9982]]]}},
            {"type": "Feature", "properties": {"levels": 2}, "geometry": {"type": "Point", "coordinates": [0, 0]}},
        ]}
        feats = heights.parse_height_features(fc)
        self.assertEqual(len(feats), 4)
        st = heights.apply_heights(self.area, self.eng, feats, source="upload", log=lambda *_: None)
        self.assertEqual((st["updated"], st["kept"], st["added"], st["unmatched"]), (1, 1, 1, 1))
        self.assertEqual((B["w62"]["levels"], B["w62"]["levels_src"]), (4, "upload"))  # floor(16,4 m / 4 m)
        self.assertEqual(B["w60"]["levels_src"], "osm")
        new = [b for b in self.area["buildings"] if b.get("added_from")]
        self.assertEqual(new[0]["levels"], 5)  # floor(21 m / 4 m); dulu 6 dengan 3,5 m
        self.assertEqual(new[0]["name"], "Gedung baru")
        Engine(self.area)  # area tetap valid

    def test_earth_engine_with_fake_module(self):
        calls = {}

        class Obj:
            def __init__(self, kind, payload=None):
                self.kind, self.payload = kind, payload

            def __getattr__(self, name):
                def f(*a, **k):
                    calls.setdefault(name, []).append((a, k))
                    if name == "reduceRegions":
                        return Obj("result", k["collection"])
                    if name == "getInfo":
                        if self.kind == "result" and isinstance(self.payload.payload, list):
                            return {"features": [{"properties": {"bid": f.payload[1]["bid"], "mean": 12.5}}
                                                 for f in self.payload.payload]}
                        return {"features": [{"geometry": {"type": "Polygon", "coordinates": [[
                            [110.0008, -7.9982], [110.0010, -7.9982], [110.0010, -7.9980], [110.0008, -7.9980]]]},
                            "properties": {"mean": 9.0}}]}
                    return self
                return f

        fake = types.ModuleType("ee")
        fake.Initialize = lambda project=None: calls.setdefault("init", project)
        fake.Geometry = types.SimpleNamespace(Rectangle=lambda c: Obj("geom", c), Polygon=lambda c: Obj("geom", c))
        fake.ImageCollection = lambda asset: (calls.setdefault("ic", asset), Obj("ic"))[1]
        fake.Feature = lambda g, p: Obj("feature", (g, p))
        fake.FeatureCollection = lambda x: Obj("fc", x)
        fake.Reducer = types.SimpleNamespace(mean=lambda: "mean")
        fake.Filter = types.SimpleNamespace(gte=lambda k, v: ("gte", k, v))
        sys.modules["ee"] = fake
        try:
            feats = heights.ee_fetch_heights(self.area, project="proyek-uji", year=2023, log=lambda *_: None)
        finally:
            sys.modules.pop("ee", None)
        self.assertEqual(calls["init"], "proyek-uji")
        self.assertEqual(calls["ic"], heights.TEMPORAL_ASSET)
        self.assertTrue(any(k.get("scale") == 4 for _, k in calls["reduceRegions"]))
        by_bid = [f for f in feats if f.get("bid")]
        self.assertEqual({f["bid"] for f in by_bid}, {"w62"})  # OSM-tagged gedung tidak diminta
        st = heights.apply_heights(self.area, self.eng, feats, source="google", log=lambda *_: None)
        self.assertEqual(st["updated"], 1)
        self.assertEqual(st["added"], 1)
        w62 = next(b for b in self.area["buildings"] if b["id"] == "w62")
        self.assertEqual((w62["levels"], w62["levels_src"]), (3, "google"))  # 12,5 m / 3,5 m

    def test_hazard_polygons(self):
        lat0, lon0 = -8.0, 110.0
        ring = [(lon0 - 0.01, lat0 - 0.01), (lon0 + 0.01, lat0 - 0.01), (lon0 + 0.01, lat0 + 0.0042),
                (lon0 - 0.01, lat0 + 0.0042), (lon0 - 0.01, lat0 - 0.01)]
        fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
                                                         "geometry": {"type": "Polygon", "coordinates": [ring]}}]}
        rings = heights.parse_hazard_polygons(fc)
        r = area_builder.apply_hazard_polygons(self.area, rings, "uji")
        N = self.area["nodes"]
        for la, hz, sf in zip(N["lat"], N["hazard"], N["safe"]):
            self.assertEqual(bool(hz), la < lat0 + 0.0042)
            if sf:
                self.assertGreater(la, lat0 + 0.0042 + 50 / 111320 * 0.9)
        self.assertGreater(r["safe_nodes"], 0)
        self.assertEqual(self.area["meta"]["hazard_method"], "polygon")


class TestTsunamiAPI(APITestBase):
    def test_levels_survey_and_uploads(self):
        h = self.login()
        # Siaga: tinggi 3 m -> masjid 2 lantai di dekat pantai jadi pilihan gedung
        r = self.client.post("/api/alerts/simulate", json={"level": "siaga", "scope": "all"}, headers=h)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["level"], "siaga")
        self.assertIn("SIAGA", r.json()["headline"])
        tb = self.client.get("/api/tables").json()
        self.assertEqual(tb["scenario"]["wave_m"], 3.0)
        # Masjid 2 lantai belum disurvei & elevasi DEM: lantai aman konservatif 5 -> bukan tujuan, bukan info.
        self.assertNotIn("Masjid Al-Ikhlas", [d["name"] for d in tb["dests"]["gedung"]])
        self.assertNotIn("Masjid Al-Ikhlas", [d["name"] for d in tb["dests"]["unverified"]])
        st = self.client.get("/api/state").json()
        self.assertEqual(st["scenario"]["level"], "siaga")
        lat, lon = synthetic.PROJ.to_latlon(420, 260)
        rr = self.client.post("/api/route", json={"lat": lat, "lon": lon, "mode": "gedung"}).json()
        self.assertTrue(rr["ok"], rr)
        # tanpa gedung terverifikasi, mode gedung tetap mengarah ke zona aman / tanah tinggi (bukan gedung unverified)
        self.assertIn(rr["routes"][0]["dest"]["type"], ("safe_zone", "tanah_tinggi"))
        self.client.post(f"/api/alerts/{r.json()['id']}/end", json={"note": "uji selesai"}, headers=h)

        # Waspada: tetap peringatan tsunami, teks "jauhi pantai"
        r = self.client.post("/api/alerts/simulate", json={"level": "waspada", "scope": "all"}, headers=h)
        self.assertIn("Jauhi pantai", r.json()["instruction"])
        self.assertEqual(app_payload(r.json())["body"][:12], "[SIMULASI] J")
        self.client.post(f"/api/alerts/{r.json()['id']}/end", json={"note": "uji selesai"}, headers=h)

        # Survei petugas: Gudang TPI 1 lantai -> ternyata 4 lantai beton
        bid = next(b["id"] for b in self.area["buildings"] if b["name"] == "Gudang TPI")
        v = self.client.get(f"/api/buildings/{bid}").json()
        self.assertFalse(v["now"]["suitable"])
        r = self.client.put(f"/api/buildings/{bid}", json={"levels": 4, "levels_confirmed": True, "condition": "baik",
                                                           "structure": "beton", "access": "24jam",
                                                           "ground_elev_m": v["building"]["elev"],
                                                           "ground_elev_method": "benchmark",
                                                           "basement": 0, "roof": True}, headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        v = r.json()
        self.assertEqual(v["building"]["levels_src"], "survey")
        self.assertTrue(v["building"]["surveyed"])
        self.assertEqual(v["tier"], "verified")
        self.assertTrue(v["levels"]["siaga"]["suitable"])
        area = self.client.get("/api/area").json()
        self.assertEqual(next(b for b in area["buildings"] if b["id"] == bid)["levels"], 4)
        # Survei bertahan setelah area dimuat ulang dari disk
        app_payload.st().ctx.clear()
        self.assertEqual(self.client.get(f"/api/buildings/{bid}").json()["building"]["levels_src"], "survey")
        self.client.delete(f"/api/buildings/{bid}/survey", headers=h)
        self.assertEqual(self.client.get(f"/api/buildings/{bid}").json()["building"]["levels"], 1)

        # Unggahan hanya untuk perencana
        hp = self.login("perencana", "perencana123")
        aid = self.area["meta"]["id"]
        self.assertEqual(self.client.post(f"/api/areas/{aid}/heights", json={"geojson": {}}, headers=h).status_code, 403)
        b = next(b for b in self.area["buildings"] if b["name"] == "Musala Nurul Huda")
        fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"height": 10},
                                                         "geometry": {"type": "Point", "coordinates": [b["lon"], b["lat"]]}}]}
        r = self.client.post(f"/api/areas/{aid}/heights", json={"geojson": fc, "name": "uji"}, headers=hp)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["kept"], 1)  # musala sudah punya building:levels OSM
        r = self.client.post(f"/api/areas/{aid}/heights/ee", json={}, headers=hp)
        self.assertEqual(r.status_code, 503)  # Earth Engine belum diatur
        lon0, lat0 = self.area["meta"]["center"]
        ring = [[lon0 - 0.05, lat0 - 0.05], [lon0 + 0.05, lat0 - 0.05], [lon0 + 0.05, lat0 - 0.002],
                [lon0 - 0.05, lat0 - 0.002], [lon0 - 0.05, lat0 - 0.05]]
        r = self.client.post(f"/api/areas/{aid}/hazard", json={"geojson": {"type": "Polygon", "coordinates": [ring]},
                                                                "name": "KRB uji"}, headers=hp)
        self.assertEqual(r.status_code, 200, r.text)
        meta = self.client.get("/api/meta").json()["area"]
        self.assertEqual(meta["hazard_method"], "polygon")
        self.assertTrue(any("KRB uji" in s for s in meta["sources"]))


class TestStaleLiveArea(APITestBase):
    def test_old_live_area_rebuilt(self):
        from backend import app as app_module
        st = app_module.STATE
        lat, lon = -0.9420, 100.3620
        r1 = self.client.post("/api/locate", json={"lat": lat, "lon": lon}).json()
        aid = r1["area_id"]
        self.assertTrue(r1["built"])
        # Turunkan versi skema seolah-olah dibuat versi lama
        st.index[aid]["schema"] = 1
        r2 = self.client.post("/api/locate", json={"lat": lat, "lon": lon}).json()
        self.assertEqual(r2["area_id"], aid)
        self.assertTrue(r2["built"])  # dibangun ulang, kini dengan gedung bertingkat
        self.assertEqual(st.index[aid]["schema"], area_builder.AREA_SCHEMA)
        area = self.client.get(f"/api/area?area={aid}").json()
        self.assertTrue(any(b["levels"] >= 2 for b in area["buildings"]))
        r3 = self.client.post("/api/locate", json={"lat": lat, "lon": lon}).json()
        self.assertFalse(r3["built"])


def app_payload(a):
    from backend import app as app_module
    return app_module._alert_payload(a)


app_payload.st = lambda: __import__("backend.app", fromlist=["STATE"]).STATE

if __name__ == "__main__":
    unittest.main()
