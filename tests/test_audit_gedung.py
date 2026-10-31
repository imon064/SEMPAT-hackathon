"""Uji regresi perbaikan audit sistem gedung tinggi (Fase 1–3).

Keamanan data publik, ID gedung stabil, persistensi unggahan saat survei, klasifikasi fungsi gedung, parsing tinggi,
geometri OSM (multipolygon, building:part), tingkat verifikasi & akses jam kerja, elevasi gedung, konsistensi kapasitas,
filter peta otomatis. Data uji SINTETIS: tidak membuktikan akurasi di lapangan.
"""
import calendar
import copy
import io
import json
import time
import unittest
import urllib.error
from unittest import mock

from backend import app as app_module
from backend import area_builder as AB
from backend import heights, live_area, osm_fetch, tsunami
from backend.engine import Engine
from backend.geo import LocalProjection

from tests.test_api import APITestBase

LAT0, LON0 = -0.95, 100.35
PROJ = LocalProjection(LAT0, LON0)
P = AB.DEFAULT_PARAMS


def ll(x, y):
    return PROJ.to_latlon(x, y)


def geom(pts):
    return [{"lat": a, "lon": b} for a, b in (ll(x, y) for x, y in pts)]


def square(cx, cy, s):
    return [(cx - s, cy - s), (cx + s, cy - s), (cx + s, cy + s), (cx - s, cy + s), (cx - s, cy - s)]


def base_elements():
    road = [(x, 0) for x in range(0, 1601, 100)]
    return [{"type": "way", "id": 1, "tags": {"highway": "residential"}, "nodes": list(range(100, 100 + len(road))),
             "geometry": geom(road)},
            {"type": "way", "id": 2, "tags": {"natural": "coastline"}, "nodes": [1, 2],
             "geometry": geom([(-100, -300), (1700, -300)])}]


def build(extra, dem=None):
    data = {"elements": base_elements() + extra}
    return AB.build_area(data, dem or (lambda pts: [2.0] * len(pts)), area_id="uji", name="uji",
                         log=lambda *_: None)


def bway(i, tags, cx, cy=40, s=20):
    return {"type": "way", "id": i, "tags": tags, "geometry": geom(square(cx, cy, s))}


def ts_local(y, mo, d, h, mi, offset_h):
    return calendar.timegm((y, mo, d, h, mi, 0)) - offset_h * 3600


# ---------------------------------------------------------------------------
# 4. Klasifikasi fungsi gedung
# ---------------------------------------------------------------------------
class TestClassification(unittest.TestCase):
    CASES = [
        # tags, jenis, eligible (dengan 4 lantai OSM), dihitung sebagai hunian untuk penduduk
        ({"building": "yes"}, "tidak_diketahui", True, True),
        ({"building": "apartments"}, "apartemen", False, True),
        ({"building": "house"}, "rumah", False, True),
        ({"building": "office"}, "komersial", True, False),
        ({"building": "commercial"}, "komersial", True, False),
        ({"building": "school"}, "sekolah", True, False),
        ({"building": "hotel"}, "hotel", True, False),
        ({"building": "xyz_tidak_dikenal"}, "tidak_diketahui", True, False),
        ({"building": "yes", "amenity": "school"}, "sekolah", True, False),
        ({"building": "yes", "shop": "supermarket"}, "komersial", True, True),
        ({"building": "yes", "office": "government"}, "kantor", True, True),
        ({"building": "yes", "building:use": "education"}, "sekolah", True, True),
        ({"building": "yes", "healthcare": "hospital"}, "kesehatan", True, True),
        ({"building": "warehouse"}, "lainnya", True, False),
    ]

    def test_jenis_kelayakan_dan_penduduk(self):
        els = [bway(1000 + i, dict(t, **{"building:levels": "4"}), 60 + i * 110) for i, (t, *_rest) in
               enumerate(self.CASES)]
        area = build(els)
        self.assertEqual(len(area["buildings"]), len(self.CASES))
        for (tags, kind, ok, dwelling), b in zip(self.CASES, area["buildings"]):
            with self.subTest(tags=tags):
                self.assertEqual(b["kind"], kind)
                self.assertEqual(tsunami.eligible(b)[0], ok)
                self.assertEqual(b["residents"] > 0, dwelling)  # model penduduk lama tidak berubah

    def test_yes_tidak_otomatis_diterima_sebagai_shelter_resmi(self):
        b = build([bway(1000, {"building": "yes", "building:levels": "5"}, 100)])["buildings"][0]
        tier, cv = tsunami.verification(b)
        self.assertEqual(tier, "unverified")
        self.assertIn("Fungsi gedung tidak diketahui", cv)
        self.assertEqual(b["tier"], "unverified")  # tier tersimpan = verification() (satu sumber, audit D-6)
        self.assertTrue(b["survey_prio"])

    def test_apartemen_perlu_survei_tapi_masuk_prioritas(self):
        b = build([bway(1000, {"building": "apartments", "building:levels": "10"}, 100)])["buildings"][0]
        self.assertEqual(tsunami.eligible(b), (False, tsunami.NEEDS_SURVEY_KINDS["apartemen"]))
        self.assertTrue(b["survey_prio"])
        b.update(levels_src="survey", surveyed=True, structure="beton", access="24jam", condition="baik")
        self.assertEqual(tsunami.verification(b)[0], "verified")

    def test_building_use_residential_bertingkat(self):
        area = build([bway(1000, {"building": "yes", "building:use": "residential", "building:levels": "6"}, 100),
                      bway(1001, {"building": "yes", "building:use": "residential", "building:levels": "2"}, 300)])
        self.assertEqual([b["kind"] for b in area["buildings"]], ["apartemen", "rumah"])

    def test_konsisten_flag_peta_dan_engine(self):
        area = build([bway(1000 + i, t, 60 + i * 110) for i, t in enumerate([
            {"building": "yes", "building:levels": "5"}, {"building": "house", "building:levels": "3"},
            {"building": "school"}, {"building": "yes"}, {"building": "office", "building:levels": "4",
                                                          "building:material": "brick"}])]
                     + [bway(1100, {"building": "yes"}, 800, s=6)])
        B = area["buildings"]
        # Peta & engine memakai SATU tier (b["tier"] = tsunami.verification); tidak ada lagi "candidate" (audit D-6).
        self.assertEqual([b["tier"] for b in B], ["unverified", "unsafe", "unsafe", "unsafe", "unsafe", "unsafe"])
        self.assertEqual([b["tier"] for b in B], [tsunami.verification(b)[0] for b in B])
        # sekolah & gedung besar (1.600 m²) tanpa data lantai masuk prioritas survei; rumah, yes kecil (144 m²),
        # dan struktur bata tidak
        self.assertEqual([b["survey_prio"] for b in B], [True, False, True, True, False, False])


# ---------------------------------------------------------------------------
# 5. Parsing tinggi
# ---------------------------------------------------------------------------
class TestHeightParsing(unittest.TestCase):
    def test_parse_height(self):
        cases = {"15": 15.0, "15 m": 15.0, "15.5m": 15.5, "15,5": 15.5, "45 ft": 13.72, "45 feet": 13.72,
                 "12'6\"": 3.81, "12;9": 9.0, "": None, "tinggi": None, "-3": None, "10 km": None, None: None,
                 "0": None}
        for v, exp in cases.items():
            with self.subTest(v=v):
                self.assertEqual(AB.parse_height_m(v), exp)

    def test_parse_levels(self):
        cases = {"3": 3, "3.5": 3, "2.5": 2, "3,5": 3, "3;4": 3, "3-4": 3, "0": None, "abc": None, "": None,
                 "500": None}
        for v, exp in cases.items():
            with self.subTest(v=v):
                self.assertEqual(AB.parse_levels(v), exp)

    def test_levels_dari_tags(self):
        per = P["height_per_level_m"]
        self.assertEqual(per, 4.0)  # keputusan final: 4 m per lantai
        self.assertEqual(AB._levels({"height": "45 ft"}), (3, "osm_height", 13.72, True, "perkiraan dari tinggi gedung"))
        self.assertEqual(AB._levels({"height": "9.5"})[:2], (2, "osm_height"))  # floor(9,5 / 4)
        self.assertEqual(AB._levels({"building:levels": "3", "height": "12"})[:4], (3, "osm", 12.0, False))
        lv = AB._levels({"building:levels": "10", "height": "9"})  # 0,9 m per lantai: konflik
        self.assertEqual(lv[:4], (2, "osm", 9.0, True))
        self.assertIn("bertentangan", lv[4])
        self.assertEqual(AB._levels({"building:levels": "0", "height": "8"})[:2], (2, "osm_height"))
        self.assertEqual(AB._levels({})[:2], (1, "default"))
        self.assertEqual(AB._levels({"height": "abc"})[:2], (1, "default"))

    def test_perkiraan_tidak_pernah_terverifikasi(self):
        b = {"kind": "sekolah", "levels": 4, "levels_src": "osm_height", "levels_est": True, "surveyed": True, "condition": "baik", "elev_src": "survei", "elev_method": "benchmark",
             "structure": "beton", "access": "24jam", "elev": 1.0, "area_m2": 400}
        tier, cv = tsunami.verification(b)
        self.assertEqual(tier, "unverified")
        self.assertIn("Jumlah lantai perkiraan dari tinggi gedung", cv)


# ---------------------------------------------------------------------------
# 6. Geometri OSM
# ---------------------------------------------------------------------------
class TestGeometry(unittest.TestCase):
    def test_way_biasa_tetap_sama(self):
        b = build([bway(1000, {"building": "school", "building:levels": "2"}, 100)])["buildings"][0]
        self.assertEqual((b["id"], b["geom"]), ("w1000", "way"))
        self.assertAlmostEqual(b["area_m2"], 1600, delta=2)

    def _rel(self, members, tags=None):
        return {"type": "relation", "id": 77, "tags": dict({"type": "multipolygon", "building": "hospital",
                                                            "building:levels": "6"}, **(tags or {})),
                "members": members}

    def test_multipolygon_dua_potongan_dan_lubang(self):
        outer = square(500, 60, 50)
        a, b = outer[:3], outer[2:]  # cincin luar dipecah dua way
        inner = square(500, 60, 10)
        rel = self._rel([{"type": "way", "ref": 11, "role": "outer", "geometry": geom(a)},
                         {"type": "way", "ref": 12, "role": "outer", "geometry": geom(list(reversed(b)))},
                         {"type": "way", "ref": 13, "role": "inner", "geometry": geom(inner)}])
        B = build([rel])["buildings"]
        self.assertEqual(len(B), 1)
        self.assertEqual((B[0]["id"], B[0]["geom"], B[0]["kind"], B[0]["levels"]), ("r77", "multipolygon", "kesehatan", 6))
        self.assertAlmostEqual(B[0]["area_m2"], 100 * 100 - 20 * 20, delta=5)

    def test_way_luar_bertag_building_tidak_ganda(self):
        ring = square(500, 60, 40)
        way = {"type": "way", "id": 11, "tags": {"building": "yes", "height": "21"}, "geometry": geom(ring)}
        rel = self._rel([{"type": "way", "ref": 11, "role": "outer", "geometry": geom(ring)}],
                        tags={"building:levels": None})
        rel["tags"].pop("building:levels")
        B = build([way, rel])["buildings"]
        self.assertEqual([b["id"] for b in B], ["r77"])
        self.assertEqual((B[0]["levels"], B[0]["levels_src"]), (5, "osm_height"))  # tag way digabung: 21 m / 4

    def test_multipolygon_tidak_valid_dilewati(self):
        rel = self._rel([{"type": "way", "ref": 11, "role": "outer", "geometry": geom(square(500, 60, 40)[:3])}])
        self.assertEqual(build([rel])["buildings"], [])

    def test_building_part_tertinggi_tidak_dijumlah(self):
        outline = bway(1000, {"building": "yes"}, 300, 60, 50)
        p1 = {"type": "way", "id": 2001, "tags": {"building:part": "yes", "building:levels": "3"},
              "geometry": geom(square(280, 60, 25))}
        p2 = {"type": "way", "id": 2002, "tags": {"building:part": "yes", "building:levels": "12"},
              "geometry": geom(square(330, 60, 10))}
        orphan = {"type": "way", "id": 2003, "tags": {"building:part": "yes", "building:levels": "30"},
                  "geometry": geom(square(1200, 300, 10))}
        B = build([outline, p1, p2, orphan])["buildings"]
        self.assertEqual(len(B), 1)  # bagian bangunan bukan bangunan sendiri
        b = B[0]
        self.assertEqual((b["levels"], b["levels_src"], b["geom"]), (12, "osm_part", "way+part"))
        self.assertAlmostEqual(b["cap_area_m2"], 400, delta=2)  # luas bagian tertinggi saja
        a = tsunami.assess(b, 3.0, P)
        self.assertEqual(a["capacity"], tsunami.shelter_capacity(b["cap_area_m2"], a["usable_floors"], P))

    def test_outline_dengan_lantai_eksplisit_menang(self):
        outline = bway(1000, {"building": "office", "building:levels": "4"}, 300, 60, 50)
        part = {"type": "way", "id": 2001, "tags": {"building:part": "yes", "building:levels": "9"},
                "geometry": geom(square(300, 60, 10))}
        b = build([outline, part])["buildings"][0]
        self.assertEqual((b["levels"], b["levels_src"]), (4, "osm"))
        self.assertNotIn("cap_area_m2", b)

    def test_geojson_multipolygon_dan_tidak_valid(self):
        big = [[100.0, -1.0], [100.001, -1.0], [100.001, -0.999], [100.0, -0.999], [100.0, -1.0]]
        small = [[100.01, -1.0], [100.0101, -1.0], [100.0101, -0.9999], [100.01, -0.9999], [100.01, -1.0]]
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"height": 12},
             "geometry": {"type": "MultiPolygon", "coordinates": [[small], [big]]}},
            {"type": "Feature", "properties": {"height": 12},
             "geometry": {"type": "Polygon", "coordinates": [[["a", "b"], [1, 2], [3, 4], [5, 6]]]}},
            {"type": "Feature", "properties": {"height": "nan"},
             "geometry": {"type": "Polygon", "coordinates": [big]}},
            {"type": "Feature", "properties": {"levels": 3, "bid": "w1"}, "geometry": None},
        ]}
        feats = heights.parse_height_features(fc)
        self.assertEqual(len(feats), 2)
        self.assertEqual(feats[0]["ring"][0], (100.0, -1.0))  # poligon terbesar, bukan yang pertama
        self.assertEqual((feats[1]["bid"], feats[1]["levels"]), ("w1", 3))


# ---------------------------------------------------------------------------
# 2. ID gedung tambahan
# ---------------------------------------------------------------------------
class TestStableIds(unittest.TestCase):
    def setUp(self):
        self.area = build([bway(1000, {"building": "school", "building:levels": "2"}, 100)])

    def _ring(self, cx, s=15):
        return [ll(x, y)[::-1] for x, y in square(cx, 90, s)]

    def feats(self):
        return [{"ring": self._ring(500), "height": 12.0, "levels": None, "name": "P", "source": "google"},
                {"ring": self._ring(900), "height": 24.0, "levels": None, "name": "Q", "source": "google"}]

    def added(self, area):
        return {b["name"]: b["id"] for b in area["buildings"] if b.get("added_from")}

    def test_urutan_berbeda_id_sama(self):
        a1, a2 = copy.deepcopy(self.area), copy.deepcopy(self.area)
        heights.apply_heights(a1, Engine(a1), self.feats(), source="google", log=lambda *_: None)
        heights.apply_heights(a2, Engine(a2), list(reversed(self.feats())), source="google", log=lambda *_: None)
        self.assertEqual(self.added(a1), self.added(a2))
        self.assertTrue(all(i.startswith("gob-") for i in self.added(a1).values()))

    def test_rebuild_dan_ulang_tidak_menggandakan(self):
        a1 = copy.deepcopy(self.area)
        heights.apply_heights(a1, Engine(a1), self.feats(), source="google", log=lambda *_: None)
        ids = self.added(a1)
        st = heights.apply_heights(a1, Engine(a1), self.feats(), source="google", log=lambda *_: None)
        self.assertEqual(st["added"], 0)
        self.assertEqual(len(a1["buildings"]), 3)
        rebuilt = build([bway(1000, {"building": "school", "building:levels": "2"}, 100)])  # area dibangun ulang
        heights.apply_heights(rebuilt, Engine(rebuilt), list(reversed(self.feats())), source="google",
                              log=lambda *_: None)
        self.assertEqual(self.added(rebuilt), ids)

    def test_collision_dan_plus_code(self):
        r = self._ring(500)
        moved = [(x + 1e-5, y) for x, y in r]  # ±1 m
        self.assertNotEqual(heights.stable_building_id("google", r), heights.stable_building_id("google", moved))
        self.assertEqual(heights.stable_building_id("google", r), heights.stable_building_id("google", r[::-1]))
        self.assertEqual(heights.stable_building_id("google", r, "6P5G+X2"),
                         heights.stable_building_id("google", moved, "6p5g+x2"))
        self.assertNotEqual(heights.stable_building_id("google", r), heights.stable_building_id("upload", r))

    def test_id_lama_g_n_dipertahankan(self):
        a = copy.deepcopy(self.area)
        heights.apply_heights(a, Engine(a), self.feats()[:1], source="google", log=lambda *_: None)
        b = next(x for x in a["buildings"] if x.get("added_from"))
        b["id"] = "g12"  # data lama
        st = heights.apply_heights(a, Engine(a), [dict(self.feats()[0], height=30.0)], source="google",
                                   log=lambda *_: None)
        self.assertEqual((st["updated"], st["added"]), (1, 0))
        self.assertEqual((b["id"], b["levels"]), ("g12", 7))  # floor(30 m / 4 m)


# ---------------------------------------------------------------------------
# 8–10. Kelayakan, akses jam kerja, elevasi, kapasitas
# ---------------------------------------------------------------------------
def _gedung(**kw):
    # Elevasi DIUKUR petugas (kepercayaan "tinggi", margin 0 m) agar uji di sini menguji akses/kapasitas/tier, bukan
    # margin DEM (margin diuji tersendiri di tests/test_milestone1.py).
    b = {"id": "x", "kind": "sekolah", "levels": 4, "levels_src": "osm", "elev": 1.0, "area_m2": 400.0,
         "elev_src": "survei", "elev_method": "benchmark",
         "structure": "tidak_tahu", "edge": 0, "t": 0.5, "perp_m": 5.0, "lat": LAT0, "lon": LON0, "name": "G"}
    b.update(kw)
    return b


class TestEligibility(unittest.TestCase):
    def test_tingkat(self):
        surv = dict(levels_src="survey", surveyed=True, condition="baik", elev_src="survei", elev_method="benchmark", access="24jam")
        self.assertEqual(tsunami.verification(_gedung(structure="beton", **surv))[0], "verified")
        self.assertEqual(tsunami.verification(_gedung(structure="baja", **surv))[0], "verified")
        self.assertEqual(tsunami.verification(_gedung(structure="bata", **surv)), ("unsafe", ["Struktur bukan beton/baja"]))
        self.assertEqual(tsunami.verification(_gedung(structure="ringan", **surv))[0], "unsafe")
        t, cv = tsunami.verification(_gedung())
        self.assertEqual(t, "unverified")
        self.assertIn("Struktur belum diperiksa", cv)
        self.assertIn("Akses masuk belum dipastikan", cv)
        t, cv = tsunami.verification(_gedung(structure="beton", levels_src="survey", surveyed=True, condition="baik", elev_src="survei", elev_method="benchmark"))
        self.assertEqual(t, "unverified")  # akses tidak diketahui -> tidak pernah dianggap aman
        self.assertEqual(tsunami.verification(_gedung(access="tidak"))[0], "unsafe")
        t, cv = tsunami.verification(_gedung(structure="beton", levels_src="survey", surveyed=True, condition="baik", elev_src="survei", elev_method="benchmark", access="jam_kerja"))
        self.assertEqual(t, "unverified")
        self.assertIn("Akses terbatas; jam buka belum dicatat petugas", cv)
        b = _gedung(structure="beton", levels_src="survey", surveyed=True, condition="baik", elev_src="survei",
                    elev_method="benchmark", access="jam_kerja", open_hours="07:00-16:00")
        t, cv = tsunami.verification(b)
        # Jam buka tercatat = data akses lengkap -> terverifikasi; buka/tutup dinilai per skenario (access_state),
        # tetapi tidak bisa menjadi shelter RESMI (wajib 24 jam).
        self.assertEqual(t, "verified")
        self.assertFalse(tsunami.can_register(b)[0])
        self.assertIn("akses 24 jam", tsunami.can_register(b)[1])

    def test_zona_waktu_dan_jam_kerja(self):
        # Cadangan bujur (zona waktu area tidak diketahui): di dekat batas zona keduanya harus terpenuhi.
        jkt, bali, ambon = 106.8, 115.2, 128.2
        self.assertEqual(tsunami._utc_offsets(jkt), [7.0])
        self.assertEqual(tsunami._utc_offsets(bali), [7.0, 8.0])
        self.assertEqual(tsunami._utc_offsets(ambon), [9.0])
        self.assertEqual(tsunami.local_time_label(ts_local(2026, 10, 31, 21, 5, 7), jkt), "21.05 WIB")
        b = _gedung(structure="beton", levels_src="survey", surveyed=True, condition="baik", elev_src="survei", elev_method="benchmark", access="jam_kerja",
                    open_hours="06:00-18:00")
        night = tsunami.assess(b, 3.0, P, at=(ts_local(2026, 10, 31, 22, 0, 7), jkt))
        day = tsunami.assess(b, 3.0, P, at=(ts_local(2026, 10, 31, 10, 0, 7), jkt))
        nodisplay = tsunami.assess(b, 3.0, P)
        self.assertFalse(night["suitable"])
        self.assertIn("Tutup saat peringatan", night["reason"])
        self.assertTrue(night["closed_now"])
        self.assertTrue(day["suitable"])
        self.assertTrue(nodisplay["suitable"])  # tanpa waktu (tampilan) tidak dibatasi; plan() memakai kondisi terburuk
        self.assertFalse(tsunami.assess(b, 3.0, P, at=tsunami.WORST_CASE)["suitable"])
        self.assertFalse(tsunami.registered_ok(b, night))
        # 17.30 WIB = 18.30 WITA: di Bali (dua zona mungkin) dianggap tutup bila salah satu zona sudah lewat jam tutup
        self.assertFalse(tsunami.assess(b, 3.0, P, at=(ts_local(2026, 10, 31, 17, 30, 7), bali))["suitable"])

    def test_elevasi_tidak_tersedia_dihitung_0(self):
        a = tsunami.assess(_gedung(elev=None), 3.0, P)
        self.assertEqual((a["depth_m"], a["min_floor"]), (3.0, 4))
        self.assertIn("Elevasi tanah tidak tersedia (dihitung 0 m)", a["caveats"])
        a = tsunami.assess(_gedung(elev=float("nan")), 3.0, P)
        self.assertEqual(a["depth_m"], 3.0)


class TestBuildingElevation(unittest.TestCase):
    def test_min_dem_jalan_dan_ketidakpastian(self):
        lat_b = ll(0, 40)[0]

        def dem(points):  # jalan 2 m; titik gedung pertama 12 m (atap ikut terukur), kedua 0,5 m
            out = []
            for la, lo in points:
                if abs(la - lat_b) < 1e-6 and lo < ll(300, 0)[1]:
                    out.append(12.0)
                elif abs(la - lat_b) < 1e-6:
                    out.append(0.5)
                else:
                    out.append(2.0)
            return out
        area = build([bway(1000, {"building": "school", "building:levels": "3"}, 100),
                      bway(1001, {"building": "school", "building:levels": "3"}, 500)], dem=dem)
        b1, b2 = area["buildings"]
        self.assertEqual((b1["elev"], b1["elev_road"], b1["elev_dem"], b1["elev_uncertain"]), (2.0, 2.0, 12.0, True))
        self.assertEqual((b2["elev"], b2["elev_dem"], b2["elev_uncertain"]), (0.5, 0.5, False))
        self.assertEqual((b1["elev_src"], b1["elev_conf"]), ("jalan_dekat", "rendah"))  # DEM 12 m ikut atap
        self.assertEqual((b2["elev_src"], b2["elev_conf"]), ("dem_gedung", "sedang"))
        far = build([bway(1000, {"building": "school", "building:levels": "3"}, 100, cy=400)])["buildings"][0]
        self.assertTrue(far["elev_uncertain"])  # jalan acuan > 150 m
        self.assertIn("Elevasi tanah kurang pasti", tsunami.caveats(far))


class TestCapacity(unittest.TestCase):
    def test_satu_rumus(self):
        a = tsunami.assess(_gedung(levels=4, elev=1.0, area_m2=500), 3.0, P)
        self.assertEqual(a["capacity"], int(500 * 2 * 0.5 / 0.93))
        self.assertEqual(a["capacity"], tsunami.shelter_capacity(500, a["usable_floors"], P))
        area = build([bway(1000, {"building": "school", "building:levels": "3"}, 100)])
        self.assertNotIn("capacity", area["buildings"][0])  # tidak ada angka kapasitas kedua di data gedung
        self.assertNotIn("climb_s", area["buildings"][0])

    def test_kapasitas_tidak_diketahui_tidak_dipakai(self):
        b = _gedung(area_m2=0)
        a = tsunami.assess(b, 3.0, P)
        self.assertEqual((a["capacity"], a["suitable"]), (0, False))
        self.assertIn("Kapasitas tidak diketahui", a["reason"])
        self.assertFalse(tsunami.registered_ok(b, a))

    def test_planner_dan_routing_konsisten(self):
        area = build([bway(1000 + i, {"building": "school", "building:levels": str(lv)}, 100 + i * 300, s=sz)
                      for i, (lv, sz) in enumerate([(3, 20), (5, 30), (4, 12)])],
                     dem=lambda pts: [1.0] * len(pts))
        for b in area["buildings"]:
            b["residents"] = 0
            # Terverifikasi (survei lengkap + elevasi terukur): hanya gedung terverifikasi yang menjadi tujuan rute.
            b.update(levels_src="survey", surveyed=True, condition="baik", elev_src="survei", elev_method="benchmark",
                     structure="beton", access="24jam")
        eng = Engine(area)
        lt = eng.live_tables(closures=set(), shelter_ids=[], points=[], alert_active=True, wave_m=3.0)
        cap_routing = {d["id"]: d["capacity"] for d in lt["dests"]["gedung"] if d.get("id")}
        plan = eng.plan(eta_min=20, delay_min=5, margin_min=2, shelter_ids=[], design_height_m=3.0)
        self.assertEqual(set(cap_routing), set(plan["destination_ids"]))
        self.assertEqual(sum(cap_routing.values()), plan["destination_capacity"])
        self.assertEqual(len(set(cap_routing.values())), 3)  # tiga shelter, kapasitas berbeda

    def test_shelter_penuh(self):
        area = build([bway(1000, {"building": "school", "building:levels": "4"}, 100, s=4)]
                     + [bway(1100 + i, {"building": "house"}, 200 + i * 40, cy=-60, s=6) for i in range(30)],
                     dem=lambda pts: [1.0] * len(pts))
        sid = area["buildings"][0]["id"]
        for b in area["buildings"][1:]:
            b["residents"] = 100.0
        eng = Engine(area)
        sh = area["buildings"][0]
        sh.update(levels_src="survey", surveyed=True, condition="baik", elev_src="survei", elev_method="benchmark", structure="beton", access="24jam")
        p = eng.plan(eta_min=6, delay_min=1, margin_min=0, shelter_ids=[sid], design_height_m=3.0)
        s = p["shelters"][0]
        self.assertGreater(s["capacity"], 0)
        self.assertEqual(s["assigned"], s["capacity"])  # penuh: tidak menampung melebihi kapasitas
        self.assertGreater(sum(p["red_after"].values()), 0)  # sisanya tetap tidak sempat


# ---------------------------------------------------------------------------
# 7. Filter peta otomatis
# ---------------------------------------------------------------------------
class TestLiveFilter(unittest.TestCase):
    def test_relevant_building(self):
        small = geom(square(0, 0, 5))
        big = geom(square(0, 0, 25))
        self.assertFalse(osm_fetch.relevant_building({"building": "house"}, big))
        self.assertFalse(osm_fetch.relevant_building({"building": "yes"}, small))
        self.assertTrue(osm_fetch.relevant_building({"building": "yes"}, big))
        self.assertTrue(osm_fetch.relevant_building({"building": "school"}, small))
        self.assertTrue(osm_fetch.relevant_building({"building": "yes", "amenity": "hospital"}, small))
        self.assertTrue(osm_fetch.relevant_building({"building": "yes", "building:levels": "3"}, small))
        self.assertFalse(osm_fetch.relevant_building({"building": "yes", "building:levels": "1"}, small))

    def test_query(self):
        q = osm_fetch.overpass_query((-1, 100, -0.9, 100.1), buildings="tall")
        for frag in ('relation["building"]["type"="multipolygon"]', '"building:part"', "length() > 80",
                     "around.parts", 'way["building"~"^(school'):
            self.assertIn(frag, q)
        legacy = osm_fetch.overpass_query((-1, 100, -0.9, 100.1), buildings="tall_legacy")
        self.assertNotIn("length()", legacy)
        self.assertNotIn("relation", legacy)
        full = osm_fetch.overpass_query((-1, 100, -0.9, 100.1), buildings=True)
        self.assertIn('relation["building"]', full)

    def test_http_400_tidak_diulang_dan_fallback(self):
        calls = []

        def bad(url, timeout=0, data=None):
            calls.append(url)
            raise urllib.error.HTTPError(url, 400, "Bad Request", {}, io.BytesIO(b""))
        with mock.patch.object(osm_fetch, "_get", bad):
            with self.assertRaises(osm_fetch.OverpassQueryError):
                osm_fetch.fetch_overpass("q", log=lambda *_: None)
        self.assertEqual(len(calls), 1)
        queries = []

        def fake_fetch(q, log=print):
            queries.append(q)
            if "length()" in q:
                raise osm_fetch.OverpassQueryError("ditolak")
            return {"elements": []}
        with mock.patch.object(osm_fetch, "fetch_overpass", fake_fetch), \
                mock.patch.object(osm_fetch, "default_dem", lambda *a, **k: "dem"):
            data, dem = live_area.internet_source(None, lambda *_: None)((-1, 100, -0.9, 100.1), "tall")
        self.assertEqual(len(queries), 2)
        self.assertNotIn("length()", queries[1])


# ---------------------------------------------------------------------------
# Skema lama
# ---------------------------------------------------------------------------
class TestUpgrade(unittest.TestCase):
    def test_skema_2_ke_3(self):
        area = build([bway(1000, {"building": "school", "height": "13"}, 100)])
        b = area["buildings"][0]
        for k in ("levels_est", "elev_src", "elev_uncertain", "elev_conf", "elev_dist_m", "geom", "tier",
                  "tier_reasons", "survey_prio", "osm_unsafe", "btag"):
            b.pop(k)
        b.update(capacity=999, climb_s=1.0, levels=3, candidate=True)  # skema lama: 13 m / 3,5 m = 3
        area["meta"]["schema"] = 2
        area["params"]["height_per_level_m"] = 3.5
        AB.upgrade_area(area)
        self.assertEqual(area["meta"]["schema"], AB.AREA_SCHEMA)
        self.assertEqual(area["params"]["height_per_level_m"], 4.0)
        self.assertTrue(b["levels_est"])
        self.assertEqual(b["levels"], 3)  # floor(13 / 4)
        self.assertIn(b["elev_src"], ("dem_gedung", "jalan_dekat"))
        self.assertIn(b["elev_conf"], ("sedang", "rendah"))
        self.assertNotIn("capacity", b)
        self.assertNotIn("candidate", b)
        # Skema < 5 tidak menyimpan tag OSM: kategori Tidak Aman tidak diketahui (None), bukan "aman" ("").
        self.assertIsNone(b["osm_unsafe"])
        self.assertEqual(b["tier"], tsunami.verification(b)[0])
        Engine(area)


# ---------------------------------------------------------------------------
# 1 & 3. API: kebocoran data, survei vs unggahan, pendaftaran, rujukan yatim
# ---------------------------------------------------------------------------
class TestApiPrivacyAndPersistence(APITestBase):
    NOTE = "kunci dipegang pak RT"

    def _survey(self, bid, **kw):
        body = {"levels": 3, "levels_confirmed": True, "condition": "baik", "structure": "beton", "access": "24jam",
                "note": self.NOTE}
        body.update(kw)
        r = self.client.put(f"/api/buildings/{bid}", json=body, headers=self.login())
        self.assertEqual(r.status_code, 200, r.text)

    def _bid(self, name):
        return next(b["id"] for b in self.area["buildings"] if b["name"] == name)

    def _no_level_data(self, bid):
        st = app_module.STATE
        area = json.loads(st.get(self.area["meta"]["id"]).area_bytes)
        next(b for b in area["buildings"] if b["id"] == bid).update(levels_src="default", levels=1, levels_est=False)
        st.save_area(area)

    def test_01_data_internal_tidak_bocor(self):
        bid = self._bid("SMP Negeri 2")
        self._survey(bid)
        h = self.login()
        c = self.area["meta"]["center"]
        self.client.post("/api/points", headers=h, json={"kind": "posko_sar", "name": "Posko Uji", "lat": c[1],
                                                         "lon": c[0]})
        a = self.client.post("/api/alerts/simulate", json={"scope": "all"}, headers=h).json()
        self.client.post(f"/api/alerts/{a['id']}/end", json={"note": "uji selesai"}, headers=h)
        secret = ("Petugas Lapangan", self.NOTE, '"_auto":', '"created_by":', '"updated_by":', '"survey":')
        for path in ("/api/area", f"/api/buildings/{bid}", "/api/state", "/api/points", "/api/alerts"):
            txt = self.client.get(path).text
            with self.subTest(path=path):
                self.assertEqual([s for s in secret if s in txt], [])
        pub = next(b for b in self.client.get("/api/area").json()["buildings"] if b["id"] == bid)
        self.assertTrue(pub["surveyed"])            # sudah disurvei (bukan berarti terverifikasi)
        self.assertEqual(pub["tier"], "verified")   # terverifikasi = tier (satu sumber, audit D-5)
        self.assertTrue(pub["verified_at"])
        self.assertEqual((pub["levels"], pub["structure"], pub["access"]), (3, "beton", "24jam"))
        last = self.client.get("/api/state").json()["last_alert"]
        self.assertEqual(last["ended_by"], "petugas")
        # terautentikasi: data internal tetap tersedia
        full = next(b for b in self.client.get("/api/area", headers=h).json()["buildings"] if b["id"] == bid)
        self.assertEqual(full["survey"]["note"], self.NOTE)
        self.assertEqual(self.client.get(f"/api/buildings/{bid}", headers=h).json()["building"]["survey"]["note"],
                         self.NOTE)
        self.assertIn("created_by", self.client.get("/api/points", headers=h).json()[0])
        self.assertIn("orphans", self.client.get("/api/state", headers=h).json())
        self.client.delete(f"/api/buildings/{bid}/survey", headers=h)

    def test_02_sse_dan_push_tanpa_nama_petugas(self):
        st = app_module.STATE
        h = self.login()
        with mock.patch.object(st.broker, "publish") as pub, mock.patch.object(st, "push_broadcast") as pb:
            a = self.client.post("/api/alerts/simulate", json={"scope": "all"}, headers=h).json()
            self.client.post(f"/api/alerts/{a['id']}/end", json={"note": "aman"}, headers=h)
        sent = [c.args[1] for c in pub.call_args_list if c.args[0] == "alert"]
        self.assertTrue(sent)
        self.assertTrue(all("created_by" not in x and x.get("ended_by") in (None, "petugas") for x in sent))
        self.assertTrue(all("Petugas Lapangan" not in json.dumps(c.args[0]) for c in pb.call_args_list))

    def test_03_unggahan_tetap_ada_setelah_survei_dibatalkan(self):
        aid = self.area["meta"]["id"]
        bid = self._bid("Gudang TPI")
        self._no_level_data(bid)  # 1. tanpa data
        hp, h = self.login("perencana", "perencana123"), self.login()

        def cur():
            return self.client.get(f"/api/buildings/{bid}").json()["building"]

        def up(levels):
            fc = {"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {"bid": bid, "levels": levels}, "geometry": None}]}
            r = self.client.post(f"/api/areas/{aid}/heights", json={"geojson": fc, "name": "uji"}, headers=hp)
            self.assertEqual(r.status_code, 200, r.text)
            return r.json()
        self.assertEqual(cur()["levels_src"], "default")
        up(2)                                                   # 2. unggahan
        self.assertEqual((cur()["levels"], cur()["levels_src"]), (2, "upload"))
        self._survey(bid, levels=5)                             # 3. survei
        r = up(3)                                               # 4. unggahan baru saat survei aktif
        self.assertEqual(r["masked_by_survey"], 1)
        self.assertEqual((cur()["levels"], cur()["levels_src"]), (5, "survey"))  # survei tidak tertimpa
        g = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"bid": bid, "height": 40, "sempat_source": "google_open_buildings"},
             "geometry": None}]}
        r = self.client.post(f"/api/areas/{aid}/heights", json={"geojson": g}, headers=hp).json()
        self.assertEqual(r["kept"], 1)  # Google tidak menimpa unggahan (peringkat lebih rendah)
        self.client.delete(f"/api/buildings/{bid}/survey", headers=h)  # 5. survei dibatalkan
        self.assertEqual((cur()["levels"], cur()["levels_src"]), (3, "upload"))  # 6. data otomatis terbaru

    def test_04_sumber_google_dari_skrip_js(self):
        aid = self.area["meta"]["id"]
        bid = self._bid("Gudang TPI")
        self._no_level_data(bid)
        g = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"bid": bid, "height": 14, "sempat_source": "google_open_buildings"},
             "geometry": None}]}
        self.client.post(f"/api/areas/{aid}/heights", json={"geojson": g}, headers=self.login("perencana", "perencana123"))
        b = self.client.get(f"/api/buildings/{bid}").json()["building"]
        self.assertEqual((b["levels"], b["levels_src"], b["levels_est"]), (3, "google", True))  # floor(14 / 4)

    def test_05_pendaftaran_wajib_terverifikasi_dan_rujukan_yatim(self):
        st = app_module.STATE
        aid = self.area["meta"]["id"]
        hp, h = self.login("perencana", "perencana123"), self.login()
        bid = self._bid("Hotel Pantai Indah")
        r = self.client.post(f"/api/shelters/{bid}", headers=hp)
        self.assertEqual(r.status_code, 400)
        self.assertIn("wajib disurvei", r.json()["detail"])
        self._survey(bid, access="jam_kerja", open_hours="07:00-16:00")
        self.assertEqual(self.client.post(f"/api/shelters/{bid}", headers=hp).status_code, 400)
        self._survey(bid)
        self.assertEqual(self.client.post(f"/api/shelters/{bid}", headers=hp).status_code, 200)
        self.client.delete(f"/api/shelters/{bid}", headers=hp)
        # rujukan ke gedung yang tidak ada (mis. ID lama setelah area dibangun ulang) tidak ditautkan ke gedung lain
        st.store.set_building_info(aid, "g99", {"levels": 9, "structure": "beton"}, "uji")
        st.store.register_shelter(aid, "g99", "uji")
        st.ctx.clear()
        orph = self.client.get("/api/state", headers=h).json()["orphans"]
        self.assertEqual(orph, {"surveys": ["g99"], "shelters": ["g99"]})
        self.assertNotIn("g99", [d.get("id") for d in self.client.get("/api/tables").json()["dests"]["aman"]])
        st.store.delete_building_info(aid, "g99")
        st.store.unregister_shelter(aid, "g99")
        self.client.delete(f"/api/buildings/{bid}/survey", headers=h)

    def test_06_jam_kerja_mengikuti_jam_peringatan(self):
        st = app_module.STATE
        aid = self.area["meta"]["id"]
        bid = self._bid("Hotel Pantai Indah")
        h = self.login()
        # elevasi tanah diukur petugas (margin 0 m) agar uji ini menguji jam buka, bukan margin DEM
        elev = next(b["elev"] for b in self.area["buildings"] if b["id"] == bid)
        self._survey(bid, access="jam_kerja", levels=6, open_hours="06:00-18:00", ground_elev_m=elev,
                     ground_elev_method="benchmark")
        c = st.get(aid)
        off = c.tz["offsets"][0]
        for hour, expect in ((10, True), (22, False)):
            ts = ts_local(2026, 10, 31, hour, 0, off)
            iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts))
            a = self.client.post("/api/alerts/simulate", json={"scope": "all", "level": "awas", "wave_m": 12},
                                 headers=h).json()
            st.store._x("UPDATE alerts SET issued_at=? WHERE id=?", (iso, a["id"]))
            st.recompute_all("uji")
            ids = [d.get("id") for d in self.client.get("/api/tables").json()["dests"]["gedung"]]
            with self.subTest(hour=hour):
                self.assertEqual(bid in ids, expect)
            self.client.post(f"/api/alerts/{a['id']}/end", json={"note": "uji selesai"}, headers=h)
        self.client.delete(f"/api/buildings/{bid}/survey", headers=h)

    def test_07_hitung_ulang_saat_pergantian_jam(self):
        st = app_module.STATE
        c = st.get(None)
        v0 = c.version
        c.valid_until = time.time() - 1  # jam buka/tutup suatu gedung baru saja lewat
        self.client.get("/api/state")
        self.assertGreater(c.version, v0)


if __name__ == "__main__":
    unittest.main()
