"""Regresi Milestone 1 (audit 1 Nov 2026, temuan D-1 s.d. D-7). Data uji SINTETIS: tidak membuktikan akurasi lapangan.

Kebijakan yang diuji (disetujui pemilik proyek 1 Nov 2026):
* verified   = boleh menjadi tujuan rute & alokasi bila lantai aman KONSERVATIF terpenuhi;
* unverified = daftar verifikasi BPBD; bagi warga hanya info TANPA rute dan TANPA alokasi;
* unsafe     = tidak pernah menjadi tujuan.
* Margin galat elevasi: tinggi (diukur petugas) 0 m, sedang 3 m, rendah 5 m; tidak tersedia -> elevasi 0 m dan tidak
  bisa verified. Genangan konservatif = H - (elev - margin); keputusan memakai yang konservatif.
"""
import json
import unittest

from backend import area_builder as AB
from backend import tsunami
from backend.app import AreaCtx
from backend.engine import Engine

from tests.test_api import APITestBase
from tests.test_audit_gedung import build, bway

P = AB.DEFAULT_PARAMS
SURVEY_OK = dict(levels_src="survey", surveyed=True, condition="baik", structure="beton", access="24jam",
                 levels_est=False)
MEASURED = dict(elev_src="survei", elev_method="benchmark")


def gedung(**kw):
    b = {"id": "x", "kind": "sekolah", "levels": 6, "elev": 1.0, "area_m2": 400.0, "edge": 0, "t": 0.5,
         "perp_m": 5.0, "lat": 0.0, "lon": 0.0, "name": "G", "osm_unsafe": ""}
    b.update(SURVEY_OK)
    b.update(kw)
    return b


# ---------------------------------------------------------------------------
# D-1 kategori bangunan tidak layak = unsafe di SEMUA jalur
# ---------------------------------------------------------------------------
class TestD1UnsafeCategories(unittest.TestCase):
    UNSAFE_TAGS = [
        {"building": "construction", "building:levels": "4"},
        {"building": "ruins", "building:levels": "3"},
        {"building": "roof", "building:levels": "2"},
        {"building": "parking", "building:levels": "5"},
        {"building": "yes", "building:levels": "4", "abandoned": "yes"},
        {"building": "yes", "building:levels": "4", "building:condition": "ruined"},
        {"building": "school", "building:levels": "4", "disused": "yes"},
        {"building": "yes", "building:levels": "4", "abandoned:amenity": "school"},
        {"building": "garage", "building:levels": "2"},
        {"building": "school", "building:levels": "4", "demolished": "yes"},
    ]
    OK_TAGS = [{"building": "yes", "building:levels": "4"}, {"building": "school", "building:levels": "4"},
               {"building": "school", "building:levels": "4", "disused": "no"}]

    def area(self):
        tags = self.UNSAFE_TAGS + self.OK_TAGS
        a = build([bway(500 + i, t, 100 + i * 120, s=15) for i, t in enumerate(tags)], dem=lambda p: [1.0] * len(p))
        self.assertEqual(len(a["buildings"]), len(tags))
        return a

    def test_tag_ke_tier(self):
        a = self.area()
        n = len(self.UNSAFE_TAGS)
        for t, b in zip(self.UNSAFE_TAGS + self.OK_TAGS, a["buildings"]):
            with self.subTest(tags=t):
                unsafe = t in self.UNSAFE_TAGS
                self.assertEqual(b["tier"] == "unsafe", unsafe)
                self.assertEqual(bool(b["osm_unsafe"]), unsafe)
                self.assertEqual(tsunami.eligible(b)[0], not unsafe)
                self.assertFalse(b["survey_prio"] and unsafe)  # tidak masuk prioritas survei
        self.assertEqual(sum(1 for b in a["buildings"] if b["tier"] == "unsafe"), n)

    def test_tidak_pernah_tujuan_walau_disurvei_lengkap(self):
        a = self.area()
        for b in a["buildings"]:
            b.update(SURVEY_OK, **MEASURED)  # bahkan dengan survei "lengkap", kategori OSM tetap Tidak Aman
        eng = Engine(a)
        unsafe_ids = {b["id"] for b in a["buildings"][:len(self.UNSAFE_TAGS)]}
        lt = eng.live_tables(closures=set(), shelter_ids=list(unsafe_ids), points=[], alert_active=True, wave_m=3.0)
        for key in ("aman", "gedung", "unverified"):
            self.assertFalse(unsafe_ids & {d.get("id") for d in lt["dests"][key]}, key)
        plan = eng.plan(eta_min=20, delay_min=5, margin_min=2, shelter_ids=list(unsafe_ids), design_height_m=3.0,
                        optimize_k=10)
        self.assertFalse(unsafe_ids & set(plan["suitable_ids"]))
        self.assertFalse(unsafe_ids & {r["id"] for r in plan["recommended"]})
        self.assertTrue(all(not s["usable"] for s in plan["shelters"]))
        for b in a["buildings"][:len(self.UNSAFE_TAGS)]:
            self.assertFalse(tsunami.can_register(b)[0])

    def test_kondisi_survei(self):
        for cond in tsunami.UNSAFE_CONDITIONS:
            self.assertEqual(tsunami.verification(gedung(condition=cond, **MEASURED))[0], "unsafe")
        self.assertEqual(tsunami.verification(gedung(condition="rusak_ringan", **MEASURED))[0], "unverified")
        self.assertEqual(tsunami.verification(gedung(condition=None, **MEASURED))[0], "unverified")
        self.assertEqual(tsunami.verification(gedung(condition="baik", **MEASURED))[0], "verified")

    def test_area_lama_tanpa_tag(self):
        a = build([bway(500, {"building": "warehouse", "building:levels": "4"}, 100)])
        b = a["buildings"][0]
        b.pop("osm_unsafe")
        b.pop("btag")
        a["meta"]["schema"] = 4
        AB.upgrade_area(a)
        self.assertIsNone(b["osm_unsafe"])
        self.assertIn("Tag kondisi OSM tidak tersimpan (data area lama)", tsunami.caveats(b))


# ---------------------------------------------------------------------------
# D-2 margin galat elevasi pada lantai aman
# ---------------------------------------------------------------------------
class TestD2ElevationMargin(unittest.TestCase):
    def a(self, H, **kw):
        return tsunami.assess(gedung(**kw), H, P)

    def test_margin_per_kepercayaan(self):
        # Siaga 3 m, tanah 1 m: nominal lantai 3; sedang +3 m -> lantai 6; rendah +5 m -> lantai 8; diukur -> 3.
        for kw, margin, floor in ((dict(elev_conf="sedang"), 3.0, 6), (dict(elev_conf="rendah"), 5.0, 8),
                                  (dict(MEASURED), 0.0, 3)):
            a = self.a(3.0, **kw)
            with self.subTest(kw=kw):
                self.assertEqual((a["elev_margin_m"], a["min_floor"], a["min_floor_nominal"]), (margin, floor, 3))
                self.assertAlmostEqual(a["depth_m"], 3.0 - (1.0 - margin))
                self.assertEqual(a["usable_floors"], max(0, 6 - floor + 1))
                self.assertEqual(a["climb_s"], round(tsunami.climb_to_floor_s(floor, P), 1))

    def test_keputusan_memakai_konservatif(self):
        # 4 lantai, tanah 3,2 m, Siaga 3 m: nominal "tidak perlu naik"; konservatif (sedang) perlu lantai 4 -> layak.
        a = self.a(3.0, levels=4, elev=3.2, elev_conf="sedang")
        self.assertEqual((a["min_floor_nominal"], a["min_floor"], a["suitable"]), (1, 4, True))
        # rendah: perlu lantai 6 > 4 lantai -> tidak layak dan tidak terdaftar resmi
        a = self.a(3.0, levels=4, elev=3.2, elev_conf="rendah")
        self.assertEqual((a["min_floor"], a["suitable"]), (6, False))
        self.assertIn("konservatif", a["reason"])
        self.assertFalse(tsunami.registered_ok(gedung(levels=4, elev=3.2, elev_conf="rendah"), a))
        # Awas 10 m, tanah 8 m: nominal lantai 3, sedang lantai 6 (gedung 6 lantai masih layak), rendah lantai 8
        self.assertEqual(self.a(10.0, elev=8.0, elev_conf="sedang")["min_floor"], 6)
        self.assertFalse(self.a(10.0, elev=8.0, elev_conf="rendah")["suitable"])

    def test_elevasi_tidak_tersedia(self):
        b = gedung(elev=None)
        a = tsunami.assess(b, 3.0, P)
        self.assertEqual((a["elev_conf"], a["elev_margin_m"], a["depth_m"], a["min_floor"]), ("tidak_ada", 0.0, 3.0, 4))
        self.assertEqual(tsunami.verification(b)[0], "unverified")  # tidak boleh otomatis verified
        self.assertFalse(a["destination"])

    def test_tanah_tinggi_konsisten(self):
        # Tanah di atas H + margin = setara tanah tinggi: tidak perlu naik gedung (dan shelter resmi tetap dipakai)
        a = self.a(3.0, elev=6.5, elev_conf="sedang")
        self.assertFalse(a["suitable"])
        self.assertIn("margin", a["reason"])
        self.assertTrue(tsunami.registered_ok(gedung(elev=6.5, elev_conf="sedang"), a))

    def test_margin_parameter_area(self):
        p = dict(P, elev_margin_m={"sedang": 1.0})
        a = tsunami.assess(gedung(elev_conf="sedang"), 3.0, p)
        self.assertEqual((a["elev_margin_m"], a["min_floor"]), (1.0, 4))


# ---------------------------------------------------------------------------
# D-3 unverified bukan tujuan alokasi; info tanpa rute
# ---------------------------------------------------------------------------
class TestD3UnverifiedNotAllocated(unittest.TestCase):
    def make(self):
        shel = [bway(900, {"building": "yes", "height": "24"}, 400, s=25),           # fungsi tdk diketahui, perkiraan
                bway(901, {"building": "school", "building:levels": "4"}, 1200, s=10)]
        houses = [bway(1100 + i, {"building": "house"}, 100 + i * 30, cy=-40, s=5) for i in range(30)]
        area = build(shel + houses, dem=lambda pts: [1.0] * len(pts))
        for b in area["buildings"]:
            if b["kind"] == "rumah":
                b["residents"] = 20.0
        area["buildings"][1].update(SURVEY_OK, **MEASURED)
        area["buildings"][0].update(MEASURED)  # elevasi terukur pun tidak membuat gedung unverified jadi tujuan
        area["nodes"]["safe"] = [0] * len(area["nodes"]["safe"])
        area["nodes"]["safe"][-1] = 1
        return area

    def test_probe_audit(self):
        area = self.make()
        eng = Engine(area)
        unv, ver = area["buildings"][0], area["buildings"][1]
        self.assertEqual((eng.assess(unv, 3.0)["tier"], eng.assess(unv, 3.0)["suitable"]), ("unverified", True))
        lt = eng.live_tables(closures=set(), shelter_ids=[ver["id"]], points=[], alert_active=True, wave_m=3.0)
        for mode in ("aman", "gedung"):
            ids = [d["id"] for d in lt["capacity"][mode]["dests"]]
            self.assertNotIn(unv["id"], ids, mode)
            self.assertTrue(all(d["tier"] == "verified" for d in lt["capacity"][mode]["dests"]))
            self.assertNotIn(unv["id"], [d.get("id") for d in lt["dests"][mode]])
        info = [d for d in lt["dests"]["unverified"] if d["id"] == unv["id"]]
        self.assertEqual(len(info), 1)
        self.assertNotIn("capacity", info[0])  # tanpa angka kapasitas: bukan rekomendasi
        # Tabel rute tidak pernah berakhir di gedung unverified
        n_dest = len(lt["dests"]["gedung"])
        for g in ("umum", "rentan"):
            self.assertTrue(all(d < n_dest for d in lt["tables"][f"gedung-{g}"]["dest"]))
        # rute online juga tidak
        for b in area["buildings"][2:8]:
            r = eng.route(b["lat"], b["lon"], mode="gedung", group="umum", closures=set(), shelter_ids=[ver["id"]],
                          points=[], alert_active=True, wave_m=3.0, alternatives=8)
            self.assertNotIn(unv["id"], [x["dest"].get("id") for x in r.get("routes", [])])

    def test_perencana_daftar_verifikasi(self):
        area = self.make()
        eng = Engine(area)
        unv = area["buildings"][0]
        p = eng.plan(eta_min=20, delay_min=5, margin_min=2, shelter_ids=[area["buildings"][1]["id"]],
                     design_height_m=3.0, optimize_k=2)
        self.assertIn(unv["id"], [v["id"] for v in p["verification_list"]])
        self.assertNotIn(unv["id"], p["destination_ids"])
        for r in p["recommended"]:
            self.assertEqual(r["requires_verification"], r["tier"] != "verified")


# ---------------------------------------------------------------------------
# D-4 satu definisi batas waktu
# ---------------------------------------------------------------------------
class TestD4Deadline(unittest.TestCase):
    def test_kebijakan_di_tabel(self):
        area = build([bway(1100 + i, {"building": "house"}, 100 + i * 30, cy=-40, s=5) for i in range(3)])
        eng = Engine(area)
        lt = eng.live_tables(closures=set(), shelter_ids=[], points=[], alert_active=True, wave_m=3.0, eta_min=20)
        d = lt["deadline"]
        self.assertEqual((d["margin_s"], d["start_delay_s"]), (120.0, 300.0))
        # anggaran jalan alokasi = ETA - jeda - margin; batas tiba absolut = peringatan + ETA - margin (sama untuk HP)
        self.assertEqual(eng.budget_s(20), 20 * 60 - d["start_delay_s"] - d["margin_s"])


# ---------------------------------------------------------------------------
# D-5 / D-6 satu tier untuk label & peta; tidak ada "candidate"
# ---------------------------------------------------------------------------
class TestD5D6SingleTier(unittest.TestCase):
    def test_survei_tidak_sama_dengan_terverifikasi(self):
        area = build([bway(901, {"building": "school", "building:levels": "4"}, 400, s=25)],
                     dem=lambda p: [1.0] * len(p))
        c = AreaCtx(area)
        b = c.engine.buildings[0]
        c.apply_survey({"building_id": b["id"], "levels": 4, "levels_confirmed": True, "structure": "tidak_tahu",
                        "access": "24jam", "condition": "baik"})
        self.assertTrue(b["surveyed"])
        self.assertNotIn("verified", b)            # kunci lama yang ambigu tidak dipakai lagi
        self.assertEqual(b["tier"], "unverified")  # struktur belum diperiksa
        info = c.engine.unverified_info(10.0) + c.engine.gedung_dests(10.0)
        for d in info:
            self.assertNotIn("verified", d)
            self.assertEqual(d["tier"], "unverified")
        c.refresh_bytes()  # endpoint survei melakukan ini setelah apply_survey
        pub = json.loads(c.public_bytes)["buildings"][0]
        self.assertEqual((pub["tier"], pub["surveyed"]), ("unverified", True))
        self.assertNotIn("candidate", pub)

    def test_tier_tersimpan_selalu_sama_dengan_verification(self):
        tags = [{"building": t, "building:levels": "5"} for t in ("yes", "school", "house", "construction",
                                                                  "apartments", "hotel", "parking")]
        area = build([bway(500 + i, t, 100 + i * 150, s=15) for i, t in enumerate(tags)])
        for b in area["buildings"]:
            self.assertEqual(b["tier"], tsunami.verification(b)[0])
            self.assertNotIn("candidate", b)


# ---------------------------------------------------------------------------
# D-7 survei tidak mengubah perkiraan menjadi pengukuran
# ---------------------------------------------------------------------------
class TestD7SurveyLevels(unittest.TestCase):
    def ctx(self):
        area = build([bway(900, {"building": "yes", "height": "24"}, 400, s=25)], dem=lambda p: [1.0] * len(p))
        return AreaCtx(area)

    def test_tanpa_konfirmasi_tetap_perkiraan(self):
        c = self.ctx()
        b = c.engine.buildings[0]
        self.assertEqual((b["levels"], b["levels_src"], b["levels_est"]), (6, "osm_height", True))
        c.apply_survey({"building_id": b["id"], "levels": 6, "structure": "beton", "access": "24jam",
                        "condition": "baik"})
        self.assertEqual((b["levels_src"], b["levels_est"], b["tier"]), ("osm_height", True, "unverified"))
        self.assertIn("Jumlah lantai perkiraan dari tinggi gedung", tsunami.caveats(b))

    def test_dengan_konfirmasi(self):
        c = self.ctx()
        b = c.engine.buildings[0]
        c.apply_survey({"building_id": b["id"], "levels": 5, "levels_confirmed": True, "structure": "beton",
                        "access": "24jam", "condition": "baik"})
        self.assertEqual((b["levels"], b["levels_src"], b["levels_est"], b["tier"]), (5, "survey", False, "verified"))

    def test_elevasi_terukur(self):
        c = self.ctx()
        b = c.engine.buildings[0]
        c.apply_survey({"building_id": b["id"], "ground_elev_m": 2.4, "ground_elev_method": "gnss_geodetik"})
        self.assertEqual((b["elev"], b["elev_src"], tsunami.elev_confidence(b)), (2.4, "survei", "tinggi"))
        c.clear_survey(b["id"])
        self.assertNotEqual(b.get("elev_src"), "survei")


class TestD7SurveyAPI(APITestBase):
    def bid(self):
        return next(b["id"] for b in self.area["buildings"] if b["name"] == "Gudang TPI")

    def test_api_menolak_lantai_tanpa_konfirmasi(self):
        h = self.login()
        bid = self.bid()
        r = self.client.put(f"/api/buildings/{bid}", headers=h,
                            json={"levels": 4, "structure": "beton", "access": "24jam", "condition": "baik"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("dihitung langsung", r.json()["detail"])
        r = self.client.put(f"/api/buildings/{bid}", headers=h,
                            json={"structure": "beton", "access": "24jam", "condition": "baik", "ground_elev_m": 2.0})
        self.assertEqual(r.status_code, 400)  # elevasi tanpa metode
        r = self.client.put(f"/api/buildings/{bid}", headers=h, json={"structure": "beton", "access": "24jam"})
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(r.json()["building"]["levels_src"], "survey")
        self.assertNotEqual(r.json()["tier"], "verified")  # jumlah lantai belum dihitung langsung
        r = self.client.put(f"/api/buildings/{bid}", headers=h, json={
            "levels": 4, "levels_confirmed": True, "structure": "beton", "access": "24jam", "condition": "baik",
            "ground_elev_m": 1.5, "ground_elev_method": "benchmark"})
        self.assertEqual(r.status_code, 200, r.text)
        v = r.json()
        self.assertEqual((v["tier"], v["building"]["levels_src"], v["building"]["elev_src"]),
                         ("verified", "survey", "survei"))
        self.assertEqual(v["levels"]["siaga"]["elev_margin_m"], 0.0)
        # bertahan setelah dimuat ulang dari disk
        from backend import app as app_module
        app_module.STATE.ctx.clear()
        v2 = self.client.get(f"/api/buildings/{bid}").json()
        self.assertEqual((v2["tier"], v2["building"]["elev"]), ("verified", 1.5))
        self.client.delete(f"/api/buildings/{bid}/survey", headers=h)
        v3 = self.client.get(f"/api/buildings/{bid}").json()
        self.assertNotEqual(v3["tier"], "verified")


if __name__ == "__main__":
    unittest.main()
