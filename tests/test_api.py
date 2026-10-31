import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend import app as app_module
from backend import bmkg, config, live_area, synthetic

FIX = Path(__file__).parent / "fixtures"


class TestBMKG(unittest.TestCase):
    def test_parse(self):
        doc = json.loads((FIX / "autogempa_sample.json").read_text())
        ev = bmkg.parse_autogempa(doc)
        self.assertAlmostEqual(ev["lat"], -9.85)
        self.assertAlmostEqual(ev["lon"], 112.40)
        self.assertAlmostEqual(ev["magnitude"], 7.2)
        self.assertEqual(ev["depth_km"], 20)
        self.assertTrue(ev["tsunami"])
        self.assertTrue(bmkg.relevant(ev, -9.5, 112.5, 600))
        doc["Infogempa"]["gempa"]["Potensi"] = "Tidak berpotensi tsunami"
        self.assertFalse(bmkg.parse_autogempa(doc)["tsunami"])
        self.assertIsNone(bmkg.parse_autogempa({"foo": 1}))


class APITestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        cls.tmp = Path(tempfile.mkdtemp())
        cls._saved = (config.USERS_PATH, config.SECRET_PATH, config.DATA_DIR, config.BMKG_POLL, config.CACHE_DIR)
        config.USERS_PATH = cls.tmp / "users.json"
        config.SECRET_PATH = cls.tmp / "secret.key"
        config.DATA_DIR = cls.tmp
        config.CACHE_DIR = cls.tmp / "cache"
        config.BMKG_POLL = False
        os.environ["SEMPAT_FAKE_OSM"] = "1"  # peta "live" memakai desa sintetis yang dipindahkan (tanpa internet)
        area = synthetic.build(log=lambda *_: None)
        ap = cls.tmp / "areas" / area["meta"]["id"] / "area.json"
        ap.parent.mkdir(parents=True)
        ap.write_text(json.dumps(area))
        app_module.STATE = app_module.AppState(cls.tmp / "areas", cls.tmp / "t.db")
        cls.client = TestClient(app_module.app)
        cls.client.__enter__()
        cls.area = area

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        app_module.STATE = None
        os.environ.pop("SEMPAT_FAKE_OSM", None)
        (config.USERS_PATH, config.SECRET_PATH, config.DATA_DIR, config.BMKG_POLL, config.CACHE_DIR) = cls._saved
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def login(self, user="petugas", pw="petugas123"):
        r = self.client.post("/api/auth/login", json={"username": user, "password": pw})
        self.assertEqual(r.status_code, 200, r.text)
        return {"Authorization": f"Bearer {r.json()['token']}"}


class TestAPI(APITestBase):
    def test_01_pages_and_data(self):
        for path in ("/", "/petugas", "/perencana", "/sw.js", "/manifest.webmanifest"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        meta = self.client.get("/api/meta").json()
        self.assertTrue(meta["demo"])
        self.assertTrue(meta["push_key"])
        area = self.client.get("/api/area").json()
        self.assertEqual(len(area["nodes"]["lat"]), self.area["meta"]["counts"]["nodes"])
        tab = self.client.get("/api/tables").json()
        self.assertIn("aman-umum", tab["tables"])
        self.assertEqual(len(tab["tables"]["aman-umum"]["time"]), len(area["nodes"]["lat"]))
        st = self.client.get("/api/state").json()
        self.assertGreaterEqual(len(st["points"]), 2)  # contoh posko
        self.assertEqual(self.client.get("/api/area?area=../../etc").status_code, 404)

    def test_02_auth_required(self):
        r = self.client.post("/api/points", json={"kind": "posko_sar", "name": "X", "lat": 0, "lon": 0})
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/api/auth/login", json={"username": "petugas", "password": "salah"})
        self.assertEqual(r.status_code, 401)
        h = self.login()
        r = self.client.post("/api/plan", json={}, headers=h)  # petugas bukan perencana
        self.assertEqual(r.status_code, 403)

    def test_03_full_flow(self):
        h = self.login()
        lat, lon = synthetic.PROJ.to_latlon(300, 260)  # warga di dekat pantai
        r = self.client.post("/api/route", json={"lat": lat, "lon": lon, "mode": "aman", "group": "umum"}).json()
        self.assertTrue(r["ok"])
        self.assertEqual(r["routes"][0]["dest"]["type"], "safe_zone")

        a = self.client.post("/api/alerts/simulate", json={"magnitude": 7.4, "eta_min": 18}, headers=h)
        self.assertEqual(a.status_code, 201, a.text)
        alert = a.json()
        self.assertEqual(alert["source"], "SIMULASI")
        self.assertEqual(self.client.post("/api/alerts/simulate", json={}, headers=h).status_code, 409)
        st = self.client.get("/api/state").json()
        self.assertEqual(st["alert"]["id"], alert["id"])

        r = self.client.post("/api/route", json={"lat": lat, "lon": lon, "mode": "posko"}).json()
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "in_hazard")

        plat, plon = synthetic.PROJ.to_latlon(500, 1550)
        p = self.client.post("/api/points", headers=h, json={
            "kind": "posko_medis", "name": "Posko Medis Uji", "lat": plat, "lon": plon,
            "status": "kosong", "services": ["medis"], "capacity": 50})
        self.assertEqual(p.status_code, 201, p.text)
        pid = p.json()["id"]
        tab = self.client.get("/api/tables").json()
        self.assertIn("Posko Medis Uji", [d["name"] for d in tab["dests"]["posko"]])

        slat, slon = synthetic.PROJ.to_latlon(450, 1450)
        r = self.client.post("/api/route", json={"lat": slat, "lon": slon, "mode": "posko"}).json()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["routes"][0]["dest"]["name"], "Posko Medis Uji")
        self.assertTrue(r["routes"][0]["dest"]["verified"])

        self.client.patch(f"/api/points/{pid}", headers=h, json={"status": "penuh"})
        r = self.client.post("/api/route", json={"lat": slat, "lon": slon, "mode": "posko"}).json()
        self.assertNotIn("Posko Medis Uji", [x["dest"]["name"] for x in r.get("routes", [])])

        c = self.client.post("/api/closures", headers=h, json={"lat": slat, "lon": slon, "note": "uji"})
        self.assertIn(c.status_code, (201, 400))
        if c.status_code == 201:
            self.client.delete(f"/api/closures/{c.json()['id']}", headers=h)

        e = self.client.post(f"/api/alerts/{alert['id']}/end", headers=h, json={"note": "BMKG mengakhiri peringatan"})
        self.assertEqual(e.status_code, 200, e.text)
        st = self.client.get("/api/state").json()
        self.assertIsNone(st["alert"])
        self.assertEqual(st["last_alert"]["id"], alert["id"])
        self.client.delete(f"/api/points/{pid}", headers=h)

    def test_04_planner(self):
        h = self.login("perencana", "perencana123")
        r = self.client.post("/api/plan", headers=h, json={"eta_min": 20, "delay_min": 5, "margin_min": 2,
                                                           "optimize_k": 3}).json()
        self.assertGreater(sum(r["red_before"].values()), 0)
        self.assertLessEqual(sum(r["red_after"].values()), sum(r["red_before"].values()))
        top = r["recommended"][0]["id"]
        self.assertIn("Belum disurvei petugas", r["recommended"][0]["caveats"])
        # Shelter resmi wajib disurvei petugas dulu (kebijakan: gedung belum terverifikasi = pilihan darurat saja)
        self.assertEqual(self.client.post(f"/api/shelters/{top}", headers=h).status_code, 400)
        lv = r["recommended"][0]["levels"]
        sv = self.client.put(f"/api/buildings/{top}", headers=self.login(),
                             json={"levels": lv, "levels_confirmed": True, "condition": "baik", "structure": "beton", "access": "24jam"})
        self.assertEqual(sv.status_code, 200, sv.text)
        self.assertEqual(self.client.post(f"/api/shelters/{top}", headers=h).status_code, 200)
        tab = self.client.get("/api/tables").json()
        self.assertEqual(tab["dests"]["aman"][0]["id"], top)
        r2 = self.client.post("/api/plan", headers=h, json={"eta_min": 20, "delay_min": 5, "margin_min": 2}).json()
        self.assertEqual(r2["shelters"][0]["id"], top)
        lat, lon = synthetic.PROJ.to_latlon(300, 260)
        info = self.client.post("/api/point-info", headers=h, json={"lat": lat, "lon": lon}).json()
        self.assertIn("PANDUAN EVAKUASI", info["guidance"])
        self.assertIn("SINTETIS", info["guidance"])
        self.client.delete(f"/api/shelters/{top}", headers=h)
        self.client.delete(f"/api/buildings/{top}/survey", headers=self.login())


class TestLiveAreas(APITestBase):
    """Peta otomatis di lokasi mana pun (jaringan diganti desa sintetis yang dipindahkan)."""

    PADANG = (-0.9470, 100.3540)

    def test_01_locate_builds_and_reuses(self):
        r = self.client.post("/api/locate", json={"lat": self.PADANG[0], "lon": self.PADANG[1]})
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertTrue(j["built"])
        aid = j["area_id"]
        self.assertTrue(aid.startswith("live-s"))
        self.assertTrue(live_area.covers(j["meta"], *self.PADANG))
        # posisi sedikit bergeser memakai area yang sama, tanpa membangun ulang
        r2 = self.client.post("/api/locate", json={"lat": self.PADANG[0] + 0.002, "lon": self.PADANG[1] - 0.002}).json()
        self.assertEqual(r2["area_id"], aid)
        self.assertFalse(r2["built"])
        area = self.client.get(f"/api/area?area={aid}").json()
        self.assertTrue(area["meta"]["live"])
        self.assertTrue(area["facilities"])
        tab = self.client.get(f"/api/tables?area={aid}").json()
        self.assertEqual(tab["area_id"], aid)
        # fasilitas OSM ikut menjadi tujuan setelah aman, ditandai belum terverifikasi
        self.assertTrue(any(d.get("verified") is False for d in tab["dests"]["posko"]))
        # rute dari posisi warga
        rt = self.client.post("/api/route", json={"lat": self.PADANG[0], "lon": self.PADANG[1], "area_id": aid}).json()
        self.assertTrue(rt["ok"], rt)
        self.assertEqual(rt["area_id"], aid)
        areas = self.client.get("/api/areas").json()["areas"]
        self.assertIn(aid, [a["id"] for a in areas])

    def test_02_alert_scope(self):
        h = self.login()
        aid = self.client.post("/api/locate", json={"lat": self.PADANG[0], "lon": self.PADANG[1]}).json()["area_id"]
        # simulasi di desa sintetis (wilayah saja) tidak sampai ke Padang
        a = self.client.post("/api/alerts/simulate", headers=h, json={"scope": "area"}).json()
        self.assertIsNone(self.client.get(f"/api/state?area={aid}").json()["alert"])
        self.assertIsNotNone(self.client.get("/api/state").json()["alert"])
        self.client.post(f"/api/alerts/{a['id']}/end", headers=h, json={"note": "selesai uji"})
        # simulasi "semua pengguna" sampai ke Padang
        a = self.client.post("/api/alerts/simulate", headers=h, json={"scope": "all"}).json()
        self.assertEqual(self.client.get(f"/api/state?area={aid}").json()["alert"]["id"], a["id"])
        tab = self.client.get(f"/api/tables?area={aid}").json()
        self.assertTrue(tab["alert_active"])
        self.client.post(f"/api/alerts/{a['id']}/end", headers=h, json={"note": "selesai uji"})

    def test_03_points_in_live_area(self):
        h = self.login()
        aid = self.client.post("/api/locate", json={"lat": self.PADANG[0], "lon": self.PADANG[1]}).json()["area_id"]
        p = self.client.post("/api/points", headers=h, json={
            "kind": "posko_sar", "name": "Posko Uji Padang", "lat": self.PADANG[0] + 0.004,
            "lon": self.PADANG[1], "area_id": aid})
        self.assertEqual(p.status_code, 201, p.text)
        self.assertEqual(p.json()["area_id"], aid)
        st = self.client.get(f"/api/state?area={aid}").json()
        self.assertIn("Posko Uji Padang", [x["name"] for x in st["points"]])
        self.assertNotIn("Posko Uji Padang", [x["name"] for x in self.client.get("/api/state").json()["points"]])
        self.client.delete(f"/api/points/{p.json()['id']}", headers=h)

    def test_04_analyze_view(self):
        h = self.login("perencana", "perencana123")
        lat, lon = -8.20, 111.10
        bbox = [lon - 0.012, lat - 0.012, lon + 0.012, lat + 0.012]
        r = self.client.post("/api/analyze", headers=h, json={"bbox": bbox, "name": "Uji Analisis"})
        self.assertEqual(r.status_code, 200, r.text)
        aid = r.json()["area_id"]
        plan = self.client.post("/api/plan", headers=h, json={"area_id": aid, "optimize_k": 2}).json()
        self.assertGreater(plan["total_people"]["umum"], 0)
        too_big = [lon - 0.1, lat - 0.1, lon + 0.1, lat + 0.1]
        self.assertEqual(self.client.post("/api/analyze", headers=h, json={"bbox": too_big}).status_code, 400)


class TestPush(APITestBase):
    def _sub(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from backend.webpush import b64u
        ua = ec.generate_private_key(ec.SECP256R1())
        pub = ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return {"endpoint": "https://fcm.googleapis.com/fcm/send/uji123", "keys": {"p256dh": b64u(pub), "auth": b64u(os.urandom(16))}}

    def test_subscribe_and_send(self):
        sub = self._sub()
        self.assertEqual(self.client.get("/api/push/key").json()["enabled"], True)
        r = self.client.post("/api/push/subscribe", json={"subscription": sub, "lat": -9.49, "lon": 112.51})
        self.assertEqual(r.status_code, 200, r.text)
        sent = []

        class Resp(io.BytesIO):
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=0):
            sent.append(req)
            return Resp(b"")

        with mock.patch("backend.webpush.urllib.request.urlopen", fake_urlopen):
            r = self.client.post("/api/push/test", json={"endpoint": sub["endpoint"]})
        self.assertEqual(r.status_code, 200, r.text)
        req = sent[0]
        self.assertEqual(req.full_url, sub["endpoint"])
        self.assertEqual(req.headers["Content-encoding"], "aes128gcm")
        self.assertTrue(req.headers["Authorization"].startswith("vapid t="))
        # peringatan dalam radius mengirim push; di luar radius tidak
        st = app_module.STATE
        with mock.patch("backend.webpush.urllib.request.urlopen", fake_urlopen):
            n0 = len(sent)
            st.push_broadcast({"type": "alert", "title": "x"}, center=(-9.6, 112.5), radius_km=100, wait=True)
            self.assertEqual(len(sent), n0 + 1)
            st.push_broadcast({"type": "alert", "title": "x"}, center=(5.5, 95.3), radius_km=100, wait=True)
            self.assertEqual(len(sent), n0 + 1)

    def test_bmkg_event_creates_alert(self):
        st = app_module.STATE
        from backend.store import now_iso
        ev = {"key": "uji-1", "issued_at": now_iso(), "magnitude": 7.6, "depth_km": 10, "lat": -10.2, "lon": 112.4,
              "region": "Selatan Jawa Timur", "potensi": "Berpotensi tsunami", "tsunami": True, "local_time": "x"}
        with mock.patch.object(st, "push_broadcast") as pb:
            app_module.handle_bmkg_event(st, ev)
            self.assertTrue(pb.called)
        a = self.client.get("/api/state").json()["alert"]
        self.assertEqual(a["source"], "BMKG")
        self.assertEqual(self.client.get("/api/quake").json()["quake"]["key"], "uji-1")
        st.store.end_alert(a["id"], "uji", "uji")
        st.recompute_all("x")


if __name__ == "__main__":
    unittest.main()
