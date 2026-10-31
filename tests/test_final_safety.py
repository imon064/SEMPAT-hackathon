"""Uji regresi Fase 5 (final patch review): 4 m per lantai, atap/kubah, tier "Perlu Verifikasi", jam buka hasil
survei + zona waktu area, waktu peringatan sampai optimizer & tabel offline, elevasi konservatif, kapasitas yang
benar-benar membatasi distribusi, provenance, dan persistensi lapisan unggahan saat area dibangun ulang.

Semua data uji SINTETIS. Lulusnya uji ini TIDAK membuktikan akurasi di lapangan.
"""
import calendar
import json
import time
import unittest

from backend import app as app_module
from backend import area_builder as AB
from backend import heights, synthetic, tsunami
from backend.engine import GROUPS, Engine

from tests.test_api import APITestBase
from tests.test_audit_gedung import P, build, bway

WIB = {"tz": "Asia/Jakarta", "offsets": [7.0], "label": "WIB", "src": "eksplisit"}
WITA = {"tz": "Asia/Makassar", "offsets": [8.0], "label": "WITA", "src": "eksplisit"}


def at_local(y, mo, d, h, mi=0, tz=WIB, window_s=0.0):
    off = tz["offsets"][0]
    return tsunami.alert_time(calendar.timegm((y, mo, d, h, mi, 0)) - off * 3600, tz, window_s)


def weekday(y, mo, d):
    return calendar.weekday(y, mo, d)  # Senin = 0


def verified(**kw):
    b = {"id": "x", "kind": "sekolah", "levels": 4, "levels_src": "survey", "surveyed": True, "condition": "baik", "elev_src": "survei", "elev_method": "benchmark", "elev": 1.0,
         "area_m2": 400.0, "structure": "beton", "access": "24jam", "levels_est": False}
    b.update(kw)
    return b


# ---------------------------------------------------------------------------
# Estimasi tinggi: 4 m per lantai
# ---------------------------------------------------------------------------
class TestHeightFourMeters(unittest.TestCase):
    def test_nilai_4m_konsisten(self):
        self.assertEqual(AB.DEFAULT_PARAMS["height_per_level_m"], 4.0)
        self.assertEqual(AB.levels_from_height(12.0), 3)
        self.assertEqual(AB.levels_from_height(15.0), 3)
        self.assertEqual(AB.levels_from_height(20.0), 5)
        self.assertEqual(AB.levels_from_height(15.99), 3)   # dibulatkan ke bawah, bukan terdekat
        self.assertEqual(AB.levels_from_height(16.0), 4)
        self.assertEqual(AB.levels_from_height(2.0), 1)     # minimal 1

    def test_tag_osm(self):
        cases = [
            ({"building:levels": "5"}, (5, "osm", False)),
            ({"height": "15m"}, (3, "osm_height", True)),
            ({"height": "15 m"}, (3, "osm_height", True)),
            ({"height": "45 ft"}, (3, "osm_height", True)),    # 13,72 m
            ({"height": "20"}, (5, "osm_height", True)),
            ({"height": "12"}, (3, "osm_height", True)),
            ({"height": "19,9"}, (4, "osm_height", True)),     # desimal koma
            ({"height": "abc"}, (1, "default", False)),
            ({"height": "-5"}, (1, "default", False)),
            ({"height": "0"}, (1, "default", False)),
            ({"building:levels": "lima"}, (1, "default", False)),
        ]
        for tags, (lv, src, est) in cases:
            with self.subTest(tags=tags):
                r = AB._levels(tags)
                self.assertEqual((r[0], r[1], r[3]), (lv, src, est))

    def test_konflik_levels_vs_height(self):
        r = AB._levels({"building:levels": "8", "height": "12"})  # 1,5 m per lantai: tidak masuk akal
        self.assertEqual(r[:2], (3, "osm"))
        self.assertTrue(r[3])
        b = build([bway(1000, {"building": "school", "building:levels": "8", "height": "12"}, 100)])["buildings"][0]
        self.assertEqual(b["levels"], 3)
        self.assertEqual(b["levels_conflict"], {"osm": 8, "osm_height": 3})
        self.assertTrue(b["survey_prio"])
        self.assertIn("Data jumlah lantai antar-sumber berbeda", " ".join(tsunami.caveats(b)))
        self.assertEqual(tsunami.verification(b)[0], "unverified")

    def test_atap_dan_kubah(self):
        self.assertEqual(AB._levels({"height": "14", "roof:height": "4"})[:2], (2, "osm_height"))   # 10 m badan
        self.assertEqual(AB._levels({"height": "16", "roof:shape": "gabled"})[:2], (3, "osm_height"))  # 16 - 4
        self.assertEqual(AB._levels({"height": "16", "roof:shape": "flat"})[:2], (4, "osm_height"))
        self.assertEqual(AB._levels({"height": "30", "roof:shape": "dome"})[:2], (1, "default"))
        self.assertEqual(AB._levels({"height": "10", "roof:height": "12"})[:2], (1, "default"))  # tidak masuk akal
        mosque = AB._levels({"height": "25", "amenity": "place_of_worship", "building": "mosque"})
        self.assertEqual(mosque[:2], (1, "default"))
        self.assertIn("kubah", mosque[4])
        # tempat ibadah DENGAN building:levels tetap dipakai
        self.assertEqual(AB._levels({"building:levels": "2", "building": "mosque"})[:2], (2, "osm"))

    def test_perkiraan_bukan_bukti_kelayakan(self):
        b = verified(levels_src="google", levels_est=True, levels=6)
        a = tsunami.assess(b, 3.0, P)
        self.assertEqual(a["tier"], "unverified")
        self.assertFalse(tsunami.registered_ok(b, a))

    def test_heights_memakai_4m_juga(self):
        area = build([bway(1000, {"building": "school"}, 100)])
        area["params"].pop("height_per_level_m")  # params lama tanpa nilai: memakai bawaan 4 m
        feats = [{"bid": "w1000", "height": 20.0, "levels": None, "name": "", "source": "google"}]
        heights.apply_heights(area, Engine(area), feats, source="google", log=lambda *_: None)
        b = area["buildings"][0]
        self.assertEqual((b["levels"], b["levels_src"], b["levels_est"]), (5, "google", True))

    def test_tinggi_tempat_ibadah_dari_google_tidak_dipakai(self):
        area = build([bway(1000, {"building": "mosque"}, 100)])
        st = heights.apply_heights(area, Engine(area), [{"bid": "w1000", "height": 24.0, "levels": None, "name": "",
                                                         "source": "google"}], log=lambda *_: None)
        self.assertEqual(st["religious_skipped"], 1)
        self.assertEqual(area["buildings"][0]["levels_src"], "default")

    def test_konflik_antar_sumber_dicatat(self):
        area = build([bway(1000, {"building": "school", "building:levels": "3"}, 100)])
        st = heights.apply_heights(area, Engine(area), [{"bid": "w1000", "height": 32.0, "levels": None, "name": "",
                                                         "source": "google"}], log=lambda *_: None)
        b = area["buildings"][0]
        self.assertEqual((st["kept"], st["conflicts"]), (1, 1))
        self.assertEqual((b["levels"], b["levels_src"]), (3, "osm"))  # peringkat sumber tetap
        self.assertEqual(b["levels_conflict"], {"osm": 3, "google": 8})
        self.assertTrue(b["survey_prio"])

    def test_upgrade_area_lama_3_5m(self):
        area = build([bway(1000, {"building": "school", "height": "21"}, 100),
                      bway(1001, {"building": "school", "height": "21"}, 300)])
        old = area["buildings"]
        old[0].update(levels=6)                                              # 21 / 3,5
        old[1].update(kind="ibadah", levels=6)                               # tinggi masjid (kubah)
        for b in old:
            b.pop("elev_conf")
        area["meta"]["schema"] = 3
        area["params"]["height_per_level_m"] = 3.5
        AB.upgrade_area(area)
        self.assertEqual(area["params"]["height_per_level_m"], 4.0)
        self.assertEqual((old[0]["levels"], old[0]["levels_src"]), (5, "osm_height"))
        self.assertEqual((old[1]["levels"], old[1]["levels_src"]), (1, "default"))


# ---------------------------------------------------------------------------
# Klasifikasi: konsisten di engine, perencana, dan rute
# ---------------------------------------------------------------------------
class TestClassificationFinal(unittest.TestCase):
    TAGS = [({"building": "yes"}, "tidak_diketahui", "unverified"),
            ({"building": "house"}, "rumah", "unsafe"),
            ({"building": "apartments"}, "apartemen", "unsafe"),
            ({"building": "school"}, "sekolah", "unverified"),
            ({"building": "commercial"}, "komersial", "unverified"),
            ({"building": "office"}, "komersial", "unverified"),
            ({"building": "hotel"}, "hotel", "unverified"),
            ({"building": "industrial"}, "lainnya", "unverified"),
            ({"building": "zzz"}, "tidak_diketahui", "unverified")]

    def test_jenis_dan_tier(self):
        area = build([bway(1000 + i, dict(t, **{"building:levels": "6"}), 60 + i * 110)
                      for i, (t, _, _) in enumerate(self.TAGS)], dem=lambda pts: [1.0] * len(pts))
        B = area["buildings"]
        for (tags, kind, tier), b in zip(self.TAGS, B):
            with self.subTest(tags=tags):
                self.assertEqual(b["kind"], kind)
                self.assertEqual(tsunami.verification(b)[0], tier)
                self.assertNotEqual(tsunami.verification(b)[0], "verified")  # tidak ada yang otomatis aman
        self.assertEqual(B[1]["tier"], "unsafe")  # rumah 6 lantai tetap bukan shelter
        eng = Engine(area)
        for b in B:
            b["residents"] = 0
        # Tidak ada yang terverifikasi -> TIDAK ada tujuan rute/alokasi gedung (kebijakan 1 Nov 2026) ...
        self.assertEqual(eng.gedung_dests(3.0), [])
        info = {d["id"]: d for d in eng.unverified_info(3.0)}
        plan = eng.plan(eta_min=20, delay_min=5, margin_min=2, shelter_ids=[], design_height_m=3.0, optimize_k=10)
        # ... gedung yang lolos lantai aman konservatif menjadi daftar verifikasi yang SAMA di warga & perencana
        self.assertEqual(set(info), set(plan["suitable_ids"]))
        self.assertEqual(set(info), {v["id"] for v in plan["verification_list"]})
        self.assertEqual(plan["destination_count"], 0)
        self.assertNotIn(B[1]["id"], info)  # rumah
        self.assertNotIn(B[2]["id"], info)  # apartemen belum disurvei
        for d in info.values():
            self.assertEqual(d["tier_label"], "Perlu Verifikasi")
            self.assertEqual(d["kind"], next(b["kind"] for b in B if b["id"] == d["id"]))
        for r in plan["recommended"]:
            self.assertEqual(r["tier_label"], "Perlu Verifikasi")
            self.assertTrue(r["requires_verification"])


# ---------------------------------------------------------------------------
# Struktur & akses (jam buka hasil survei, zona waktu area)
# ---------------------------------------------------------------------------
class TestStructureAccess(unittest.TestCase):
    def test_struktur(self):
        self.assertEqual(tsunami.verification(verified())[0], "verified")
        self.assertEqual(tsunami.verification(verified(structure="baja"))[0], "verified")
        self.assertEqual(tsunami.verification(verified(structure="bata"))[0], "unsafe")
        self.assertEqual(tsunami.verification(verified(structure="ringan"))[0], "unsafe")   # kayu/bambu
        t, cv = tsunami.verification(verified(structure="tidak_tahu"))
        self.assertEqual(t, "unverified")
        self.assertIn("Struktur belum diperiksa", cv)
        self.assertEqual(tsunami.TIER_LABEL["unverified"], "Perlu Verifikasi")

    def test_parse_jam_buka(self):
        self.assertEqual(tsunami.parse_open_hours("07:00-16:00"), [(420, 960)])
        self.assertEqual(tsunami.parse_open_hours("07.00 - 16.00"), [(420, 960)])
        self.assertEqual(tsunami.parse_open_hours("04:30-06:00, 11:30-20:00"), [(270, 360), (690, 1200)])
        self.assertEqual(tsunami.parse_open_hours("18:00-06:00"), [(1080, 360)])
        self.assertEqual(tsunami.parse_open_hours("00:00-24:00"), [(0, 1440)])
        for bad in ("", None, "abc", "25:00-26:00", "07:00-07:00", "07:00", "7-16"):
            with self.subTest(bad=bad):
                self.assertIsNone(tsunami.parse_open_hours(bad))
        self.assertEqual(tsunami.parse_open_days("01234"), frozenset(range(5)))
        self.assertEqual(tsunami.parse_open_days(None), frozenset(range(7)))

    def test_akses(self):
        g = verified(access="jam_kerja", open_hours="07:00-16:00")
        day = at_local(2026, 11, 2, 10, tz=WITA)
        night = at_local(2026, 11, 2, 20, tz=WITA)
        self.assertEqual(tsunami.access_state(verified(), night)[0], "open")
        self.assertEqual(tsunami.access_state(verified(access="tidak"), day)[0], "closed")
        self.assertEqual(tsunami.access_state(g, day)[0], "open")
        self.assertEqual(tsunami.access_state(g, night)[0], "closed")
        # jam di zona area (WITA), bukan WIB: 10.00 WIB = 11.00 WITA (buka); 06.30 WITA tutup
        self.assertEqual(tsunami.access_state(g, at_local(2026, 11, 2, 6, 30, tz=WITA))[0], "closed")
        # harus terbuka sampai perkiraan gelombang tiba: 15.50 + 20 menit melewati jam tutup
        self.assertEqual(tsunami.access_state(g, at_local(2026, 11, 2, 15, 50, tz=WITA, window_s=1200))[0], "closed")
        self.assertEqual(tsunami.access_state(g, at_local(2026, 11, 2, 15, 30, tz=WITA, window_s=1200))[0], "open")

    def test_lewat_tengah_malam_dan_hari(self):
        night_shift = verified(access="jam_kerja", open_hours="18:00-06:00")
        self.assertEqual(tsunami.access_state(night_shift, at_local(2026, 11, 3, 2))[0], "open")
        self.assertEqual(tsunami.access_state(night_shift, at_local(2026, 11, 3, 12))[0], "closed")
        weekdays = verified(access="jam_kerja", open_hours="07:00-16:00", open_days="01234")
        mon, sun = (2026, 11, 2), (2026, 11, 8)
        self.assertEqual((weekday(*mon), weekday(*sun)), (0, 6))
        self.assertEqual(tsunami.access_state(weekdays, at_local(*mon, 10))[0], "open")
        self.assertEqual(tsunami.access_state(weekdays, at_local(*sun, 10))[0], "closed")

    def test_akses_tidak_diketahui_dan_jam_belum_dicatat(self):
        unknown = verified(access=None, verified=False, levels_src="osm")
        a = tsunami.assess(unknown, 3.0, P, at=at_local(2026, 11, 2, 2))
        self.assertTrue(a["suitable"])                       # tetap pilihan darurat ...
        self.assertEqual(a["tier_label"], "Perlu Verifikasi")  # ... dengan label jelas
        self.assertIn("Akses masuk belum dipastikan", a["caveats"])
        self.assertEqual(a["access_state"], "unknown")
        nohours = verified(access="jam_kerja")
        self.assertEqual(tsunami.access_state(nohours, None)[0], "limited")
        a = tsunami.assess(nohours, 3.0, P, at=at_local(2026, 11, 2, 10))
        self.assertFalse(a["suitable"])                      # jam tidak diketahui = dianggap terkunci
        self.assertIn("jam buka belum dicatat", a["reason"])
        self.assertTrue(nohours["access"] and tsunami.survey_priority(nohours, P))

    def test_tempat_ibadah_tidak_otomatis_24_jam(self):
        b = {"id": "m", "kind": "ibadah", "levels": 3, "levels_src": "osm", "elev": 1.0, "area_m2": 500.0,
             "structure": "beton"}
        t, cv = tsunami.verification(b)
        self.assertEqual(t, "unverified")
        self.assertIn("Akses masuk belum dipastikan", cv)
        self.assertFalse(tsunami.registered_ok(b, tsunami.assess(b, 3.0, P)))

    def test_kondisi_terburuk(self):
        self.assertEqual(tsunami.access_state(verified(access="jam_kerja", open_hours="07:00-16:00"),
                                              tsunami.WORST_CASE)[0], "closed")
        self.assertEqual(tsunami.access_state(verified(access="jam_kerja", open_hours="00:00-24:00"),
                                              tsunami.WORST_CASE)[0], "open")
        self.assertEqual(tsunami.access_state(verified(), tsunami.WORST_CASE)[0], "open")

    def test_zona_waktu(self):
        self.assertEqual(tsunami.timezone_for_province("Jawa Timur"), "Asia/Jakarta")
        self.assertEqual(tsunami.timezone_for_province("Provinsi Bali"), "Asia/Makassar")
        self.assertEqual(tsunami.timezone_for_province("Kalimantan Tengah"), "Asia/Pontianak")
        self.assertEqual(tsunami.timezone_for_province("Kalimantan Selatan"), "Asia/Makassar")
        self.assertEqual(tsunami.timezone_for_province("Maluku Utara"), "Asia/Jayapura")
        self.assertEqual(tsunami.timezone_for_province("West Java"), "Asia/Jakarta")
        self.assertIsNone(tsunami.timezone_for_province("Atlantis"))
        # Kalimantan Tengah di bujur 114,5° BT: bujur menebak WIB/WITA, zona eksplisit WIB saja
        meta = {"center": [114.5, -2.0]}
        self.assertEqual(tsunami.area_timezone(meta)["src"], "bujur")
        self.assertEqual(tsunami.area_timezone(meta)["offsets"], [7.0, 8.0])
        meta["timezone"] = "Asia/Pontianak"
        self.assertEqual(tsunami.area_timezone(meta)["offsets"], [7.0])
        self.assertEqual(tsunami.tz_offsets("UTC+8"), [8.0])


class TestTimeThroughEngine(unittest.TestCase):
    """Waktu peringatan dipakai rute, tabel offline, optimizer, dan rute online — bukan hanya assess()."""

    def setUp(self):
        houses = [bway(1100 + i, {"building": "house"}, 150 + i * 60, cy=-60, s=8) for i in range(20)]
        self.area = build([bway(1000, {"building": "school", "building:levels": "4"}, 300),
                           bway(1001, {"building": "hotel", "building:levels": "6"}, 900)] + houses,
                          dem=lambda pts: [1.0] * len(pts))
        self.area["meta"]["timezone"] = "Asia/Jakarta"
        B = self.area["buildings"]
        B[0].update(levels_src="survey", surveyed=True, condition="baik", elev_src="survei", elev_method="benchmark", structure="beton", access="24jam")
        B[1].update(levels_src="survey", surveyed=True, condition="baik", elev_src="survei", elev_method="benchmark", structure="beton", access="jam_kerja",
                    open_hours="07:00-16:00")
        self.eng = Engine(self.area)
        self.A, self.H = B[0]["id"], B[1]["id"]

    def test_tabel_rute_optimizer(self):
        day, night = at_local(2026, 11, 2, 10), at_local(2026, 11, 2, 22)
        for at, hotel_open in ((day, True), (night, False)):
            with self.subTest(open=hotel_open):
                lt = self.eng.live_tables(closures=set(), shelter_ids=[], points=[], alert_active=True,
                                          wave_m=3.0, at=at)
                ids = [d.get("id") for d in lt["dests"]["gedung"]]
                self.assertIn(self.A, ids)
                self.assertEqual(self.H in ids, hotel_open)
                b = self.area["buildings"][1]
                r = self.eng.route(b["lat"], b["lon"], mode="gedung", group="umum", closures=set(), shelter_ids=[],
                                   points=[], alert_active=True, wave_m=3.0, alternatives=8, at=at)
                self.assertEqual(self.H in [x["dest"].get("id") for x in r["routes"]], hotel_open)
                p = self.eng.plan(eta_min=20, delay_min=2, margin_min=0, shelter_ids=[], design_height_m=3.0,
                                  optimize_k=5, at=at)
                self.assertEqual(self.H in p["suitable_ids"], hotel_open)
                if not hotel_open:
                    self.assertIn(self.H, p["closed_ids"])
                    self.assertNotIn(self.H, [x["id"] for x in p["recommended"]])
        worst = self.eng.plan(eta_min=20, delay_min=2, margin_min=0, shelter_ids=[], design_height_m=3.0,
                              optimize_k=5)
        self.assertNotIn(self.H, worst["suitable_ids"])  # bawaan perencanaan: kondisi terburuk
        self.assertEqual(worst["time"]["mode"], "terburuk")

    def test_valid_until_untuk_offline(self):
        at = at_local(2026, 11, 2, 10, window_s=1200)
        lt = self.eng.live_tables(closures=set(), shelter_ids=[], points=[], alert_active=False, wave_m=3.0, at=at)
        # status hotel berubah pukul 15.40 WIB (16.00 dikurangi 20 menit perkiraan waktu tiba)
        self.assertEqual(lt["valid_until"], at_local(2026, 11, 2, 15, 40).ts)
        later = tsunami.alert_time(lt["valid_until"] + 60, WIB, 1200)
        lt2 = self.eng.live_tables(closures=set(), shelter_ids=[], points=[], alert_active=False, wave_m=3.0, at=later)
        self.assertNotIn(self.H, [d.get("id") for d in lt2["dests"]["gedung"]])

    def test_shelter_resmi_wajib_terverifikasi_dan_terbuka(self):
        day, night = at_local(2026, 11, 2, 10), at_local(2026, 11, 2, 22)
        self.assertEqual([d["id"] for d in self.eng.shelter_dests([self.A, self.H], 3.0, day)], [self.A])  # hotel: jam
        self.area["buildings"][0]["structure"] = "bata"  # unsafe: tidak lolos walau terdaftar
        self.assertEqual(self.eng.shelter_dests([self.A], 3.0, night), [])
        lt = self.eng.live_tables(closures=set(), shelter_ids=[self.A], points=[], alert_active=True, wave_m=3.0,
                                  at=night)
        self.assertNotIn(self.A, [d.get("id") for d in lt["dests"]["gedung"]])
        p = self.eng.plan(eta_min=20, delay_min=2, margin_min=0, shelter_ids=[self.A], design_height_m=3.0,
                          optimize_k=5, at=night)
        self.assertEqual((p["shelters"][0]["usable"], p["shelters"][0]["capacity"]), (False, 0))
        self.assertNotIn(self.A, [r["id"] for r in p["recommended"]])


# ---------------------------------------------------------------------------
# Elevasi
# ---------------------------------------------------------------------------
class TestElevationFinal(unittest.TestCase):
    def test_kasus(self):
        f = AB.building_elevation
        d = f(3.0, 4.0, 20.0, P)       # DEM langsung (lebih rendah dari jalan)
        self.assertEqual((d["elev"], d["elev_src"], d["elev_conf"], d["elev_dist_m"]), (3.0, "dem_gedung", "sedang", 20.0))
        d = f(None, 4.0, 50.0, P)      # interpolasi jalan dekat
        self.assertEqual((d["elev"], d["elev_src"], d["elev_conf"]), (4.0, "jalan_dekat", "rendah"))
        d = f(None, 4.0, 400.0, P)     # jalan jauh: TIDAK dipakai
        self.assertEqual((d["elev"], d["elev_src"], d["elev_conf"], d["elev_dist_m"]),
                         (None, "tidak_tersedia", "tidak_ada", 400.0))
        d = f(6.0, 2.0, 400.0, P)      # DEM ada, jalan jauh: DEM saja, kepercayaan rendah
        self.assertEqual((d["elev"], d["elev_src"], d["elev_conf"]), (6.0, "dem_gedung", "rendah"))
        d = f(12.0, 2.0, 30.0, P)      # DEM ikut atap: pakai jalan dekat yang lebih rendah
        self.assertEqual((d["elev"], d["elev_src"], d["elev_conf"]), (2.0, "jalan_dekat", "rendah"))

    def test_tidak_tersedia_konservatif(self):
        far = verified(**AB.building_elevation(None, 6.0, 400.0, P))
        a = tsunami.assess(far, 3.0, P)
        self.assertEqual((a["depth_m"], a["min_floor"], a["elev_conf"]), (3.0, 4, "tidak_ada"))  # dihitung 0 m
        self.assertIn("Elevasi tanah tidak tersedia (dihitung 0 m)", a["caveats"])
        near = verified(**AB.building_elevation(None, 6.0, 30.0, P))
        self.assertIn("Elevasi tanah kurang pasti", tsunami.assess(near, 3.0, P)["caveats"])

    def test_build_area_jalan_jauh_dan_dem_kosong(self):
        def dem(points):  # DEM tidak tersedia (NaN) untuk titik gedung; jalan 2 m
            return [float("nan") if abs(la - points[-1][0]) < 1e-9 and abs(lo - points[-1][1]) < 1e-9 else 2.0
                    for la, lo in points]
        b = build([bway(1000, {"building": "school", "building:levels": "4"}, 100, cy=400)], dem=dem)["buildings"][0]
        self.assertGreater(b["perp_m"], 150)
        self.assertEqual((b["elev"], b["elev_src"], b["elev_conf"]), (None, "tidak_tersedia", "tidak_ada"))
        self.assertEqual(b["hazard"], 1)  # tanpa elevasi: dianggap di zona bahaya (konservatif)
        Engine({**build([bway(1000, {"building": "school"}, 100)]), "buildings": [b]})

    def test_gedung_tambahan_jalan_jauh(self):
        area = build([bway(1000, {"building": "school", "building:levels": "2"}, 100)])
        from tests.test_audit_gedung import ll
        ring = [(lo, la) for la, lo in (ll(x, y) for x, y in [(800, 230), (840, 230), (840, 270), (800, 270), (800, 230)])]
        st = heights.apply_heights(area, Engine(area), [{"ring": ring, "height": 20.0, "levels": None, "name": "",
                                                         "source": "google", "uid": None}], log=lambda *_: None)
        self.assertEqual(st["added"], 1)
        nb = next(b for b in area["buildings"] if b.get("added_from"))
        self.assertEqual((nb["elev"], nb["elev_src"]), (None, "tidak_tersedia"))


# ---------------------------------------------------------------------------
# Kapasitas membatasi distribusi
# ---------------------------------------------------------------------------
def area_len(eng, e):
    return eng.area["edges"]["len"][e] if hasattr(eng, "area") else eng.elen[e]


class TestCapacityDistribution(unittest.TestCase):
    def make(self, cap_sizes=(6, 30), people=25.0, n_houses=24):
        shelters = [bway(1000 + i, {"building": "school", "building:levels": "4"}, x, s=s)
                    for i, (x, s) in enumerate(zip((300, 1300), cap_sizes))]
        houses = [bway(1100 + i, {"building": "house"}, 120 + i * 25, cy=-40, s=5) for i in range(n_houses)]
        area = build(shelters + houses, dem=lambda pts: [1.0] * len(pts))
        for b in area["buildings"]:
            b["residents"] = people if b["kind"] == "rumah" else 0.0
        for b in area["buildings"][:2]:
            b.update(levels_src="survey", surveyed=True, condition="baik", elev_src="survei", elev_method="benchmark", structure="beton", access="24jam")
        # tanpa zona aman di dekatnya: warga memang harus ke gedung (agar kapasitas teruji)
        area["nodes"]["safe"] = [0] * len(area["nodes"]["safe"])
        area["nodes"]["safe"][-1] = 1
        return area

    def loads_from_tables(self, eng, lt, mode):
        """Hitung ulang beban tiap tujuan langsung dari bundel HP (tabel + overrides), tidak memakai info engine.

        Sejak alokasi LP: penghuni bangunan yang alokasinya tidak bisa diwakili tabel dikirim sebagai `overrides`
        (jumlah orang per tujuan); HP di bangunan itu mengikuti override. Penduduk = orang bulat (eng.demand()).
        Aturan pencocokan sama dengan core.js overrideAt: ruas sama, dalam 25 m sepanjang ruas, yang terdekat; satu
        override (bisa gabungan beberapa bangunan di titik yang sama) dihitung SEKALI.
        """
        dests = lt["dests"][mode]
        load = {}
        dem = dict(eng.demand())
        counted = set()
        for bi, b in enumerate(eng.buildings):
            for g in GROUPS:
                n = dem.get(bi, {}).get(g, 0)
                if n <= 0:
                    continue
                rec, bd = None, None
                for r in lt["overrides"][f"{mode}-{g}"]:
                    if r["e"] == b["edge"]:
                        d = abs(r["t"] - b["t"]) * area_len(eng, b["edge"])
                        if d <= 25.0 and (bd is None or d < bd):
                            rec, bd = r, d
                if rec is not None:
                    if id(rec) in counted:
                        continue
                    counted.add(id(rec))
                    for o in rec["opts"]:
                        if o["d"] >= 0 and dests[o["d"]].get("id"):
                            load[dests[o["d"]]["id"]] = load.get(dests[o["d"]]["id"], 0.0) + o["n"]
                    continue
                tab = lt["tables"][f"{mode}-{g}"]
                cu, cv = eng._end_costs(b, g)
                u, v = eng.eu[b["edge"]], eng.ev[b["edge"]]
                ends = [(cu + tab["time"][u], u), (cv + tab["time"][v], v)]
                ends = [e for e in ends if tab["time"][e[1]] >= 0]
                if not ends:
                    continue
                node = min(ends)[1]
                x = node
                while tab["next"][x] != -1:
                    x = tab["next"][x]
                di = tab["dest"][x]
                if di >= 0 and dests[di].get("id"):
                    load[dests[di]["id"]] = load.get(dests[di]["id"], 0.0) + n
        return load

    def test_tidak_melebihi_kapasitas_dan_dialihkan(self):
        area = self.make()
        eng = Engine(area)
        sa, sb = area["buildings"][0]["id"], area["buildings"][1]["id"]
        caps = {sid: eng.assess(eng.buildings[eng.bindex[sid]], 3.0)["capacity"] for sid in (sa, sb)}
        self.assertLess(caps[sa], caps[sb])                           # kapasitas berbeda
        total = sum(b["residents"] for b in area["buildings"])
        self.assertGreater(total, caps[sa])                           # A pasti kelebihan bila tanpa batas
        self.assertLessEqual(total, caps[sa] + caps[sb])
        lt = eng.live_tables(closures=set(), shelter_ids=[sa, sb], points=[], alert_active=True, wave_m=3.0)
        for mode in ("aman", "gedung"):
            with self.subTest(mode=mode):
                load = self.loads_from_tables(eng, lt, mode)
                for sid, cap in caps.items():
                    self.assertLessEqual(load.get(sid, 0.0), cap + 1e-6)
                self.assertGreater(load.get(sb, 0.0), 0)              # kelebihan A dialihkan ke B
                self.assertEqual(lt["capacity_check"][mode]["overflow"], [])
                pa = next(d for d in lt["dests"][mode] if d.get("id") == sa)
                self.assertTrue(pa["crowded"] and pa["pen_s"] > 0)
        # tanpa batas kapasitas (logika lama) A menerima lebih dari kapasitasnya
        no_pen = eng._sources_for(eng.shelter_dests([sa, sb], 3.0), "umum", True)
        from backend.engine import reverse_multi_source
        dist, _, dest = reverse_multi_source(eng.radj["umum"], no_pen)
        near_a = sum(eng._people(b)["umum"] for b in eng.buildings if b["residents"] and
                     dest[min((eng._end_costs(b, "umum")[0] + dist[eng.eu[b["edge"]]], eng.eu[b["edge"]]),
                              (eng._end_costs(b, "umum")[1] + dist[eng.ev[b["edge"]]], eng.ev[b["edge"]]))[1]] == 0)
        self.assertGreater(near_a, caps[sa] * 0.85 - 1)

    def test_rute_online_sama_dengan_tabel(self):
        area = self.make()
        eng = Engine(area)
        sa, sb = area["buildings"][0]["id"], area["buildings"][1]["id"]
        lt = eng.live_tables(closures=set(), shelter_ids=[sa, sb], points=[], alert_active=True, wave_m=3.0)
        pen = {d["id"]: d.get("pen_s", 0) for d in lt["dests"]["aman"] if d.get("id")}
        checked = 0
        for b in eng.buildings:
            if not b["residents"]:
                continue
            r = eng.route(b["lat"], b["lon"], mode="aman", group="umum", closures=set(), shelter_ids=[sa, sb],
                          points=[], alert_active=True, wave_m=3.0)
            self.assertTrue(r["ok"])
            d = r["routes"][0]["dest"]
            self.assertIn(d.get("id"), (sa, sb, None))
            if d.get("id") == sa:
                self.assertEqual(d.get("pen_s"), pen[sa])
            checked += 1
        self.assertGreater(checked, 10)

    def test_penuh_kapasitas_tidak_diketahui_dan_planner(self):
        area = self.make()
        area["buildings"][1]["area_m2"] = 0.0  # B: luas tidak diketahui -> kapasitas 0, bukan tak terbatas
        eng = Engine(area)
        sa, sb = area["buildings"][0]["id"], area["buildings"][1]["id"]
        self.assertEqual([d["id"] for d in eng.shelter_dests([sa, sb], 3.0)], [sa])
        lt = eng.live_tables(closures=set(), shelter_ids=[sa, sb], points=[], alert_active=True, wave_m=3.0)
        cap_a = eng.assess(eng.buildings[0], 3.0)["capacity"]
        load = self.loads_from_tables(eng, lt, "aman")
        self.assertLessEqual(load.get(sa, 0.0), cap_a)               # penuh: sisanya ke zona aman yang jauh
        self.assertNotIn(sb, load)
        p = eng.plan(eta_min=30, delay_min=0, margin_min=0, shelter_ids=[sa, sb], design_height_m=3.0)
        s = {x["id"]: x for x in p["shelters"]}
        self.assertEqual(s[sa]["capacity"], cap_a)                   # perencana & rute: kapasitas sama
        self.assertLessEqual(s[sa]["assigned"], cap_a)
        self.assertEqual((s[sb]["capacity"], s[sb]["assigned"], s[sb]["usable"]), (0, 0, False))
        cap_tab = {d["id"]: d["capacity"] for d in lt["dests"]["aman"] if d.get("id")}
        self.assertEqual(cap_tab, {sa: cap_a})

    def test_banyak_gedung_penuh_berantai(self):
        """Kelebihan yang dialihkan membuat gedung tetangga ikut penuh (berantai): semua tetap <= kapasitas."""
        import random
        from backend.geo import LocalProjection
        rng = random.Random(7)
        n = 40
        proj = LocalProjection(-8.0, 110.0)
        N = {"lat": [], "lon": [], "elev": [], "hazard": [], "safe": []}
        for j in range(n):
            for i in range(n):
                la, lo = proj.to_latlon(i * 50.0, j * 50.0)
                N["lat"].append(la)
                N["lon"].append(lo)
                N["elev"].append(1.0)
                N["hazard"].append(int(j < n - 2))
                N["safe"].append(int(j >= n - 2))
        E = {"u": [], "v": [], "len": [], "kind": []}
        for j in range(n):
            for i in range(n):
                a = j * n + i
                for b in ([a + 1] if i + 1 < n else []) + ([a + n] if j + 1 < n else []):
                    E["u"].append(a)
                    E["v"].append(b)
                    E["len"].append(50.0)
                    E["kind"].append("road")
        bl = []
        for k in range(600):
            e = rng.randrange(len(E["u"]))
            bl.append({"id": f"h{k}", "name": "", "kind": "rumah", "levels": 1, "levels_src": "default",
                       "structure": "tidak_tahu", "area_m2": 80, "lat": 0, "lon": 0, "edge": e, "t": 0.5,
                       "perp_m": 10.0, "elev": 1.0, "residents": 4.0})
        for k in range(30):
            e = rng.randrange(len(E["u"]))
            # gedung TERVERIFIKASI (survei lengkap, elevasi terukur): hanya tier ini yang menjadi tujuan alokasi
            bl.append({"id": f"s{k}", "name": f"S{k}", "kind": "sekolah", "levels": 5, "levels_src": "survey",
                       "surveyed": True, "condition": "baik", "elev_src": "survei", "elev_method": "benchmark",
                       "access": "24jam", "structure": "beton", "area_m2": 50, "lat": N["lat"][E["u"][e]], "lon": N["lon"][E["u"][e]],
                       "edge": e, "t": 0.5, "perp_m": 10.0, "elev": 1.0, "residents": 0.0})
        area = {"meta": {"center": [110.0, -8.0], "id": "uji"}, "params": dict(P), "nodes": N, "edges": E,
                "buildings": bl}
        eng = Engine(area)
        lt = eng.live_tables(closures=set(), shelter_ids=[], points=[], alert_active=True, wave_m=3.0)
        caps = {d["id"]: d["capacity"] for d in lt["dests"]["gedung"] if d.get("id")}
        self.assertEqual(len(caps), 30)
        demand = sum(b["residents"] for b in bl)
        self.assertGreater(demand, 0.6 * sum(caps.values()))       # pemakaian tinggi: pengalihan berantai
        load = self.loads_from_tables(eng, lt, "gedung")
        over = {k: (v, caps[k]) for k, v in load.items() if v > caps[k] + 1e-6}
        self.assertEqual(over, {})
        self.assertEqual(lt["capacity_check"]["gedung"]["overflow"], [])
        self.assertGreater(sum(1 for d in lt["dests"]["gedung"] if d.get("pen_s")), 3)

    def test_tanpa_penghuni_tanpa_penalti(self):
        area = self.make(people=0.0)
        eng = Engine(area)
        lt = eng.live_tables(closures=set(), shelter_ids=[b["id"] for b in area["buildings"][:2]], points=[],
                             alert_active=True, wave_m=3.0)
        self.assertFalse(any(d.get("pen_s") for d in lt["dests"]["aman"] + lt["dests"]["gedung"]))


# ---------------------------------------------------------------------------
# API: kebocoran, provenance, persistensi saat rebuild, jam skenario, zona waktu
# ---------------------------------------------------------------------------
class TestFinalApi(APITestBase):
    NOTE = "kunci dipegang pak Budi"

    def _bid(self, name):
        return next(b["id"] for b in self.area["buildings"] if b["name"] == name)

    def _survey(self, bid, **kw):
        body = {"levels": 4, "levels_confirmed": True, "condition": "baik", "structure": "beton", "access": "24jam",
                "note": self.NOTE}
        body.update(kw)
        return self.client.put(f"/api/buildings/{bid}", json=body, headers=self.login())

    def test_01_tidak_bocor_di_semua_endpoint_publik(self):
        bid = self._bid("Hotel Pantai Indah")
        self.assertEqual(self._survey(bid, access="jam_kerja", open_hours="07:00-16:00", levels=6).status_code, 200)
        h = self.login()
        a = self.client.post("/api/alerts/simulate", json={"scope": "all", "level": "awas", "wave_m": 12},
                             headers=h).json()
        b = next(x for x in self.area["buildings"] if x["id"] == bid)
        secret = ("Petugas Lapangan", self.NOTE, '"_auto"', '"created_by"', '"updated_by"', '"survey":', "petugas123")
        paths = ["/api/area", f"/api/buildings/{bid}", "/api/state", "/api/points", "/api/alerts", "/api/tables",
                 "/api/meta"]
        for path in paths:
            with self.subTest(path=path):
                txt = self.client.get(path).text
                self.assertEqual([s for s in secret if s in txt], [])
        r = self.client.post("/api/route", json={"lat": b["lat"], "lon": b["lon"], "mode": "gedung"}).text
        self.assertEqual([s for s in secret if s in r], [])
        pub = next(x for x in self.client.get("/api/area").json()["buildings"] if x["id"] == bid)
        self.assertEqual((pub["access"], pub["open_hours"]), ("jam_kerja", "07:00-16:00"))  # tetap ada untuk warga
        full = self.client.get(f"/api/buildings/{bid}", headers=h).json()["building"]
        self.assertEqual(full["survey"]["note"], self.NOTE)            # terautentikasi: tetap lengkap
        self.client.post(f"/api/alerts/{a['id']}/end", json={"note": "uji selesai"}, headers=h)
        self.client.delete(f"/api/buildings/{bid}/survey", headers=h)

    def test_02_akses_bawaan_tidak_dianggap_24jam(self):
        bid = self._bid("SMP Negeri 2")
        r = self.client.put(f"/api/buildings/{bid}", json={"levels": 3, "levels_confirmed": True, "structure": "beton"}, headers=self.login())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["building"]["access"])
        self.assertEqual(r.json()["tier_label"], "Perlu Verifikasi")
        hp = self.login("perencana", "perencana123")
        self.assertEqual(self.client.post(f"/api/shelters/{bid}", headers=hp).status_code, 400)
        r = self._survey(bid, access="jam_kerja")  # tanpa jam buka -> ditolak
        self.assertEqual(r.status_code, 400)
        self.assertIn("jam buka", r.json()["detail"])
        self.assertEqual(self._survey(bid, access="jam_kerja", open_hours="25:00-26:00").status_code, 400)
        self.client.delete(f"/api/buildings/{bid}/survey", headers=self.login())

    def _no_data_bid(self, skip=0):
        """Bangunan tanpa data lantai di data sumber (unggahan tidak tertahan peringkat OSM)."""
        return [b["id"] for b in self.area["buildings"] if b["levels_src"] == "default"][skip]

    def test_03_unggahan_manual_bukan_google(self):
        aid = self.area["meta"]["id"]
        bid = self._no_data_bid(0)
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"bid": bid, "height": 20}, "geometry": None}]}
        hp = self.login("perencana", "perencana123")
        r = self.client.post(f"/api/areas/{aid}/heights", json={"geojson": fc, "source": "google"}, headers=hp)
        self.assertEqual(r.status_code, 200, r.text)
        b = self.client.get(f"/api/buildings/{bid}").json()["building"]
        self.assertEqual((b["levels_src"], b["levels"]), ("upload", 5))  # bukan "google" walau diminta

    def test_04_unggahan_dan_survei_bertahan_setelah_rebuild(self):
        st = app_module.STATE
        aid = self.area["meta"]["id"]
        bid = self._no_data_bid(1)
        hp, h = self.login("perencana", "perencana123"), self.login()
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"bid": bid, "levels": 7}, "geometry": None}]}
        self.assertEqual(self.client.post(f"/api/areas/{aid}/heights", json={"geojson": fc, "name": "uji rebuild"},
                                          headers=hp).status_code, 200)
        lon0, lat0 = self.area["meta"]["center"]
        ring = [[lon0 - 0.05, lat0 - 0.05], [lon0 + 0.05, lat0 - 0.05], [lon0 + 0.05, lat0 - 0.002],
                [lon0 - 0.05, lat0 - 0.002], [lon0 - 0.05, lat0 - 0.05]]
        self.assertEqual(self.client.post(f"/api/areas/{aid}/hazard", json={
            "geojson": {"type": "Polygon", "coordinates": [ring]}, "name": "KRB uji"}, headers=hp).status_code, 200)
        self.assertEqual(self._survey(bid, levels=3).status_code, 200)
        # area dibangun ulang dari sumber asli (tanpa unggahan) lalu dimuat lagi
        (st.areas_dir / aid / "area.json").write_text(json.dumps(synthetic.build(log=lambda *_: None)))
        st.ctx.clear()
        cur = self.client.get(f"/api/buildings/{bid}").json()["building"]
        self.assertEqual((cur["levels"], cur["levels_src"]), (3, "survey"))   # survei tetap menang
        self.assertEqual(self.client.get("/api/area").json()["meta"]["hazard_method"], "polygon")
        self.client.delete(f"/api/buildings/{bid}/survey", headers=h)
        cur = self.client.get(f"/api/buildings/{bid}").json()["building"]
        self.assertEqual((cur["levels"], cur["levels_src"]), (7, "upload"))   # unggahan pulih
        # dimuat ulang lagi: lapisan tidak diterapkan dua kali
        st.ctx.clear()
        area = self.client.get("/api/area").json()
        self.assertEqual(len(area["meta"]["layers_applied"]), len(st.store.list_layers(aid)))

    def test_05_jam_skenario_perencana(self):
        bid = self._bid("Hotel Pantai Indah")
        # elevasi tanah diukur petugas (margin 0 m): uji ini tentang jam skenario, bukan margin DEM
        elev = next(b["elev"] for b in self.area["buildings"] if b["id"] == bid)
        self.assertEqual(self._survey(bid, access="jam_kerja", open_hours="07:00-16:00", levels=6, ground_elev_m=elev,
                                      ground_elev_method="benchmark").status_code, 200)
        hp = self.login("perencana", "perencana123")

        def plan(t):
            r = self.client.post("/api/plan", json={"design_height_m": 12, "optimize_k": 5, "scenario_time": t},
                                 headers=hp)
            self.assertEqual(r.status_code, 200, r.text)
            return r.json()
        self.assertNotIn(bid, plan("terburuk")["suitable_ids"])
        night = plan("02:00")
        self.assertNotIn(bid, night["suitable_ids"])
        self.assertIn(bid, night["closed_ids"])
        self.assertIn(bid, plan("10:00")["suitable_ids"])
        self.assertIn("WIB", plan("10:00")["time"]["label"])
        r = self.client.post("/api/plan", json={"scenario_time": "peringatan"}, headers=hp)
        self.assertEqual(r.status_code, 400)  # tidak ada peringatan aktif
        self.assertEqual(self.client.post("/api/plan", json={"scenario_time": "25:00"}, headers=hp).status_code, 422)
        self.client.delete(f"/api/buildings/{bid}/survey", headers=self.login())

    def test_06_zona_waktu_area(self):
        st = app_module.STATE
        aid = self.area["meta"]["id"]
        self.assertEqual(self.client.get("/api/meta").json()["timezone"]["src"], "eksplisit")
        hp = self.login("perencana", "perencana123")
        r = self.client.put(f"/api/areas/{aid}/timezone", json={"timezone": "Asia/Makassar"}, headers=hp)
        self.assertEqual(r.status_code, 200, r.text)
        st.ctx.clear()  # bertahan setelah dimuat ulang (disimpan di DB)
        tz = self.client.get("/api/meta").json()["timezone"]
        self.assertEqual((tz["label"], tz["src"]), ("WITA", "perencana"))
        self.assertEqual(self.client.put(f"/api/areas/{aid}/timezone", json={"timezone": "Mars/Olympus"},
                                         headers=hp).status_code, 400)
        self.assertEqual(self.client.put(f"/api/areas/{aid}/timezone", json={"timezone": "Asia/Jakarta"},
                                         headers=self.login()).status_code, 403)
        self.client.put(f"/api/areas/{aid}/timezone", json={"timezone": "Asia/Jakarta"}, headers=hp)

    def test_07_tabel_membawa_valid_until(self):
        bid = self._bid("Hotel Pantai Indah")
        self.assertEqual(self._survey(bid, access="jam_kerja", open_hours="00:01-23:59", levels=6).status_code, 200)
        t = self.client.get("/api/tables").json()
        self.assertTrue(t["valid_until"] and t["valid_until"] > time.time())
        self.assertIn("time", t["scenario"])
        self.client.delete(f"/api/buildings/{bid}/survey", headers=self.login())


if __name__ == "__main__":
    unittest.main()
