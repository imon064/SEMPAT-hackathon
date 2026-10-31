"""Regresi Milestone 2: grid penduduk, payload per peran, geometri (building:min_level, duplikat), provenance.

Semua grid di sini SINTETIS (berlabel synthetic) — tidak membuktikan apa pun tentang data HRSL nyata.
"""
import gzip
import json
import unittest

from backend import app as app_module
from backend import population as POP
from backend import synthetic, tsunami
from backend.app import AreaCtx, public_area
from backend.engine import Engine

from tests.test_api import APITestBase
from tests.test_audit_gedung import P, build, bway, geom, square


class TestParseCsv(unittest.TestCase):
    def test_header_bervariasi(self):
        for head, row in (("longitude,latitude,population_2020", "100.35,-0.95,3.5"),
                          ("Lat,Lon,Population", "-0.95,100.35,3.5"),
                          ("x,y,value", "100.35,-0.95,3.5")):
            cells = POP.parse_csv(f"{head}\n{row}\n")
            self.assertEqual(cells, [(100.35, -0.95, 3.5)], head)

    def test_baris_rusak_dan_nol_dibuang(self):
        cells = POP.parse_csv("longitude,latitude,population\n100.3,-0.9,0\n100.3,-0.9,abc\n100.3,-0.9,2\n,,\n")
        self.assertEqual(cells, [(100.3, -0.9, 2.0)])

    def test_header_tidak_dikenal(self):
        with self.assertRaises(ValueError):
            POP.parse_csv("a,b,c\n1,2,3\n")
        with self.assertRaises(ValueError):
            POP.parse_csv("longitude,latitude,population\n")


class TestApplyGrid(unittest.TestCase):
    def area(self):
        houses = [bway(1100 + i, {"building": "house"}, 100 + i * 200, cy=-40, s=5) for i in range(4)]
        a = build(houses + [bway(2000, {"building": "school", "building:levels": "3"}, 900, s=20)])
        w, s, e, n = a["meta"]["bbox"]
        a["meta"]["bbox"] = [w, s - 0.001, e, n + 0.001]  # jalan uji lurus: beri tinggi bbox
        return a

    def test_distribusi_tetangga_virtual_dan_label(self):
        a = self.area()
        B = a["buildings"]
        h0, h1 = B[0], B[1]
        c0 = (round(h0["lon"] * 3600) / 3600, round(h0["lat"] * 3600) / 3600)  # sel berisi rumah 0
        # sel tanpa bangunan, 20 m dari rumah 1 -> rumah 1; sel jauh di jalan -> asal virtual; sel di laut -> tak teralokasi
        from tests.test_audit_gedung import ll
        x1 = ll(300, -15)
        far = ll(1500, 5)
        sea = ll(800, -2500)
        g = POP.make_grid([(c0[0], c0[1], 10.0), (x1[1], x1[0], 7.0), (far[1], far[0], 5.0), (sea[1], sea[0], 4.0)],
                          gid="uji", name="uji", source="sintetis", license="-", synthetic=True)
        a["meta"]["bbox"] = [min(x[1] for x in (sea, far, x1)) - 0.01, sea[0] - 0.01, far[1] + 0.01, 0.0]
        info = POP.apply_grids(a, [g], POP.edge_snapper(a))
        self.assertEqual(info["method"], "grid")
        self.assertTrue(info["synthetic"])
        self.assertIn("SINTETIS", info["label"])
        self.assertAlmostEqual(h0["residents"], 10.0)
        self.assertAlmostEqual(h1["residents"], 7.0)
        self.assertEqual(B[2]["residents"], 0.0)               # rumah tanpa sel = 0 (grid berlaku untuk area)
        virt = [b for b in a["buildings"] if b.get("virtual")]
        self.assertEqual(len(virt), 1)
        self.assertAlmostEqual(virt[0]["residents"], 5.0)
        self.assertEqual(info["unassigned"], 4.0)
        self.assertAlmostEqual(info["total"], 22.0)
        # idempoten: terapkan ulang tidak menggandakan asal virtual
        POP.apply_grids(a, [g], POP.edge_snapper(a))
        self.assertEqual(len([b for b in a["buildings"] if b.get("virtual")]), 1)
        # tanpa grid: kembali ke model lama, berlabel jelas
        POP.apply_grids(a, [], POP.edge_snapper(a))
        self.assertEqual(a["meta"]["population"]["method"], "model")
        self.assertEqual(a["meta"]["population"]["label"], POP.MODEL_LABEL)
        self.assertEqual(h0["residents"], P["persons_per_house"])
        self.assertFalse(any(b.get("virtual") for b in a["buildings"]))

    def test_virtual_bukan_bangunan(self):
        a = self.area()
        from tests.test_audit_gedung import ll
        far = ll(1500, 5)
        g = POP.make_grid([(far[1], far[0], 5.0)], gid="u", name="u", source="s", license="-", synthetic=True)
        POP.apply_grids(a, [g], POP.edge_snapper(a))
        eng = Engine(a)
        v = next(b for b in a["buildings"] if b.get("virtual"))
        self.assertEqual(eng.assess(v, 3.0)["tier"], "unsafe")
        self.assertNotIn(v["id"], [d["id"] for d in eng.unverified_info(3.0) + eng.gedung_dests(3.0)])
        self.assertIn(eng.bindex[v["id"]], [bi for bi, _ in eng.demand()])  # tetap asal alokasi
        self.assertNotIn(v["id"], [b["id"] for b in public_area(a)["buildings"]])

    def test_density_hanya_dari_grid(self):
        a = self.area()
        self.assertEqual(POP.density_cells(a, [])["count"], 0)  # model 4/rumah tidak pernah jadi peta kepadatan
        g = POP.synthetic_grid_for(a)
        d = POP.density_cells(a, [g])
        self.assertGreater(d["count"], 0)
        self.assertTrue(d["synthetic"])


class TestGeometry(unittest.TestCase):
    def test_min_level_kapasitas(self):
        parts = [{"type": "way", "id": 3000, "tags": {"building:part": "yes", "building:levels": "6",
                                                       "building:min_level": "3"}, "geometry": geom(square(100, 40, 10))}]
        a = build([bway(2000, {"building": "school"}, 100, s=20)] + parts, dem=lambda p: [1.0] * len(p))
        b = a["buildings"][0]
        self.assertEqual((b["levels"], b.get("min_level")), (6, 3))
        b.update(elev_src="survei", elev_method="benchmark")
        x = tsunami.assess(b, 3.0, P)
        # lantai aman 3, tetapi bagian tertinggi baru mulai di lantai 4 -> lantai 4..6 = 3 lantai terpakai
        self.assertEqual((x["min_floor"], x["usable_floors"]), (3, 3))

    def test_duplikat_dibuang(self):
        a = build([bway(2000, {"building": "school", "name": "SD"}, 100, s=20),
                   bway(2001, {"building": "yes"}, 100.5, s=20), bway(2002, {"building": "yes"}, 160, s=20)])
        self.assertEqual([b["name"] for b in a["buildings"]], ["SD", ""])

    def test_snapshot_dan_model(self):
        area = synthetic.build(log=lambda *_: None)
        self.assertIn("built_at", area["meta"]["snapshot"])
        eng = Engine(area)
        lt = eng.live_tables(closures=set(), shelter_ids=[], points=[], alert_active=False)
        m = lt["model"]
        self.assertEqual(len(m["params_sha"]), 12)
        self.assertEqual(m["population"], "model")
        eng.params["persons_per_house"] = 5
        self.assertNotEqual(eng.model_info()["params_sha"], m["params_sha"])


class TestPublicPayload(unittest.TestCase):
    def test_ringkas_dan_tanpa_debug(self):
        area = synthetic.build(log=lambda *_: None)
        c = AreaCtx(area)
        full, pub = json.loads(c.area_bytes), json.loads(c.public_bytes)
        self.assertLess(len(pub["buildings"]), len(full["buildings"]))
        self.assertEqual(pub["meta"]["buildings_total"], len(full["buildings"]))
        self.assertTrue(pub["meta"]["demo_start"])
        for b in pub["buildings"]:
            for k in ("residents", "elev_dem", "elev_road", "edge", "t", "perp_m", "survey", "_auto", "btag"):
                self.assertNotIn(k, b)
        self.assertLess(len(gzip.compress(c.public_bytes)), len(gzip.compress(c.area_bytes)))


class TestPopulationAPI(APITestBase):
    def test_unggah_grid_menjadi_penduduk_area(self):
        hp, h = self.login("perencana", "perencana123"), self.login()
        st = app_module.STATE
        c = st.get(None)
        self.assertEqual(c.meta["population"]["method"], "model")
        g = POP.synthetic_grid_for(c.area, people_per_cell=9.0)
        csv = "longitude,latitude,population\n" + "\n".join(f"{lo},{la},{po}" for lo, la, po in
                                                             zip(g["lon"], g["lat"], g["pop"]))
        self.assertEqual(self.client.post("/api/population?gid=uji&name=uji", content=csv, headers=h).status_code, 403)
        r = self.client.post("/api/population?gid=uji-sintetis&name=Grid%20uji&synthetic=true&license=-",
                             content=csv, headers=hp)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertIn(c.id, r.json()["areas"])
        c2 = st.get(None)
        pop = c2.meta["population"]
        self.assertEqual(pop["method"], "grid")
        self.assertIn("SINTETIS", pop["label"])
        self.assertAlmostEqual(pop["total"], g["total"], delta=0.5)
        st.recompute(c2.id, "uji")
        tb = self.client.get("/api/tables").json()
        self.assertEqual(tb["capacity"]["aman"]["population"]["method"], "grid")
        self.assertEqual(tb["model"]["population"], "grid")
        d = self.client.get(f"/api/areas/{c2.id}/population", headers=hp).json()
        self.assertEqual(d["cells"]["count"], g["count"])
        self.assertTrue(d["cells"]["synthetic"])
        self.assertEqual(self.client.get(f"/api/areas/{c2.id}/population").status_code, 401)
        self.assertEqual(self.client.delete("/api/population/uji-sintetis", headers=hp).status_code, 200)
        self.assertEqual(st.get(None).meta["population"]["method"], "model")

    def test_peta_otomatis_tanpa_rumah_mendapat_asal_virtual(self):
        hp = self.login("perencana", "perencana123")
        st = app_module.STATE
        r = self.client.post("/api/locate", json={"lat": -0.942, "lon": 100.362}).json()
        live = st.get(r["area_id"])
        self.assertEqual(live.meta["population"]["method"], "model")
        self.assertEqual(live.engine.demand(), [])                    # tanpa rumah -> tanpa penduduk
        lo0, la0 = live.meta["center"]
        cells = [(lo0 + i * 0.0006, la0 + j * 0.0006, 8.0) for i in range(-4, 5) for j in range(-4, 5)]
        csv = "longitude,latitude,population\n" + "\n".join(f"{a},{b},{c}" for a, b, c in cells)
        rr = self.client.post("/api/population?gid=uji-live&name=uji&synthetic=true", content=csv, headers=hp)
        self.assertEqual(rr.status_code, 201, rr.text)
        live2 = st.get(r["area_id"])
        self.assertGreater(live2.meta["population"]["virtual_origins"], 0)
        self.assertTrue(live2.engine.demand())
        st.recompute(live2.id, "uji")
        st.wait_allocations(120)
        tb = self.client.get(f"/api/tables?area={live2.id}").json()
        self.assertNotEqual(tb["capacity"]["aman"]["status"], "tanpa_penduduk")
        self.client.delete("/api/population/uji-live", headers=hp)


if __name__ == "__main__":
    unittest.main()
