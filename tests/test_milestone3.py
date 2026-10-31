"""Regresi Milestone 3: lapisan peta turunan (zona bahaya aturan/resmi, grid waktu evakuasi, kepadatan penduduk).

Semua area di sini SINTETIS. Uji ini memeriksa kejujuran label dan konsistensi metode, bukan akurasi lapangan.
"""
import math
import unittest

from backend import mapgrid, synthetic
from backend.engine import Engine
from backend.geo import LocalProjection
from backend.speed import GROUP_SPEED_MPS

from tests.test_api import APITestBase


def _area():
    return synthetic.build(log=lambda *_: None)


def _cells(g):
    """Iterasi (lon, lat, nilai) pusat setiap sel di grid run-length."""
    for i0, j, n, v in g["runs"]:
        for i in range(i0, i0 + n):
            yield (g["origin"][0] + (i + 0.5) * g["dlon"], g["origin"][1] + (j + 0.5) * g["dlat"], v)


class TestHazardView(unittest.TestCase):
    def test_aturan_berlabel_perkiraan(self):
        a = _area()
        a["meta"]["hazard_method"] = "rule"
        hv = mapgrid.hazard_view(a)
        self.assertEqual(hv["method"], "rule")
        self.assertFalse(hv["official"])
        self.assertIn("PERKIRAAN", hv["label"])
        self.assertIn("BUKAN peta resmi", hv["label"])
        self.assertTrue(hv["runs"], "desa sintetis punya simpul zona bahaya")

    def test_tanpa_simpul_bahaya_grid_kosong(self):
        a = _area()
        a["meta"]["hazard_method"] = "rule"
        a["nodes"]["hazard"] = [False] * len(a["nodes"]["hazard"])
        self.assertEqual(mapgrid.hazard_view(a)["runs"], [])

    def test_poligon_resmi_tidak_digrid(self):
        a = _area()
        a["meta"]["hazard_method"] = "polygon"
        a["meta"]["sources"] = list(a["meta"].get("sources", [])) + ["Zona bahaya: InaRISK uji"]
        hv = mapgrid.hazard_view(a)
        self.assertTrue(hv["official"])
        self.assertNotIn("runs", hv)
        self.assertIn("InaRISK uji", hv["label"])


class TestTimeGrid(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.area = _area()
        eng = Engine(cls.area)
        cls.tab = eng.live_tables(closures=[], shelter_ids=[], points=[], alert_active=False)["tables"]["aman-umum"]

    def grid(self, **kw):
        return mapgrid.time_grid(self.area, self.tab, speed_mps=GROUP_SPEED_MPS["umum"], **kw)

    def test_pita_sesuai_waktu_simpul_terdekat(self):
        g = self.grid()
        self.assertEqual(g["cell_m"], 30.0)
        self.assertEqual(g["band_s"], 180.0)
        self.assertIn("bukan akurasi", g["method"])
        N = self.area["nodes"]
        c = self.area["meta"]["center"]
        proj = LocalProjection(c[1], c[0])
        xy = [proj.to_xy(la, lo) for la, lo in zip(N["lat"], N["lon"])]
        ok_nodes = [k for k, t in enumerate(self.tab["time"]) if t is not None and t >= 0]
        checked = 0
        for lon, lat, v in list(_cells(g))[::37]:
            x, y = proj.to_xy(lat, lon)
            d, k = min((math.hypot(xy[k][0] - x, xy[k][1] - y), k) for k in ok_nodes)
            # sel terisi hanya bila simpul berwaktu ada dalam max_dist (toleransi setengah diagonal sel)
            self.assertLessEqual(d, g["max_dist_m"] + 1.0)
            exp = min(g["max_bands"], int((self.tab["time"][k] + d / GROUP_SPEED_MPS["umum"]) // g["band_s"]))
            self.assertLessEqual(abs(v - exp), 1, (v, exp))  # proyeksi ulang -> boleh beda 1 pita di batas
            checked += 1
        self.assertGreater(checked, 20)

    def test_sel_jauh_dari_jalan_kosong(self):
        g = self.grid()
        filled = sum(n for _, _, n, _ in g["runs"])
        self.assertLess(filled, g["nx"] * g["ny"], "sebagian sel harus kosong (jauh dari jalan)")

    def test_pita_maksimum_dibatasi(self):
        g = self.grid(max_bands=2)
        self.assertTrue(all(0 <= v <= 2 for _, _, _, v in g["runs"]))

    def test_area_besar_memperbesar_sel_dan_mencatatnya(self):
        old = mapgrid.MAX_CELLS
        try:
            mapgrid.MAX_CELLS = 2000
            g = self.grid()
            self.assertGreater(g["cell_m"], 30.0)
            self.assertLessEqual(g["nx"] * g["ny"], 2000 * 1.05)
        finally:
            mapgrid.MAX_CELLS = old


class TestMapEndpoints(APITestBase):
    def aid(self):
        return self.area["meta"]["id"]

    def test_time_grid_hanya_staf(self):
        self.assertEqual(self.client.get(f"/api/areas/{self.aid()}/time-grid").status_code, 401)
        h = self.login("perencana", "perencana123")
        r = self.client.get(f"/api/areas/{self.aid()}/time-grid?mode=aman&group=rentan", headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        g = r.json()
        self.assertEqual((g["mode"], g["group"]), ("aman", "rentan"))
        self.assertIn("0,5 m/s", g["label"])
        r2 = self.client.get(f"/api/areas/{self.aid()}/time-grid?mode=aman&group=rentan", headers=h)
        self.assertEqual(r2.json()["version"], g["version"])
        self.assertEqual(self.client.get(f"/api/areas/{self.aid()}/time-grid?mode=x", headers=h).status_code, 422)

    def test_kepadatan_tanpa_grid_tidak_dikarang(self):
        self.assertEqual(self.client.get(f"/api/areas/{self.aid()}/population").status_code, 401)
        h = self.login()
        r = self.client.get(f"/api/areas/{self.aid()}/population", headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        # tanpa grid diunggah: tidak ada sel kepadatan, model 4 orang/rumah tidak dipetakan sebagai kepadatan
        self.assertEqual(body["cells"]["count"], 0, body)
        self.assertEqual(body["population"]["method"], "model")
        self.assertIn("PERKIRAAN MODEL", body["population"]["label"])

    def test_area_publik_membawa_zona_bahaya_berlabel(self):
        a = self.client.get("/api/area").json()
        hv = a.get("hazard_view") or {}
        self.assertIn("label", hv)
        if not hv.get("official"):
            self.assertIn("BUKAN peta resmi", hv["label"])


if __name__ == "__main__":
    unittest.main()
