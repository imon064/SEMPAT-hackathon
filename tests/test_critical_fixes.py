"""Uji regresi untuk tiga bug kritis hasil audit backend:

C1  Shelter terdaftar tetap dijadikan tujuan walau survei menyatakan struktur/akses tidak layak.
C2  Akun demo (petugas123/perencana123) aktif di server publik.
C3  Notifikasi push dikirim berurutan sehingga satu HP lambat menunda peringatan ke HP lain.
"""
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from backend import app as app_module
from backend import area_builder, auth, config, synthetic, tsunami
from backend.engine import Engine

from tests.test_api import APITestBase

ENV_KEYS = ("SEMPAT_PUBLIC", "RENDER_EXTERNAL_URL", "SEMPAT_PETUGAS_PASSWORD", "SEMPAT_PERENCANA_PASSWORD")


def _b(**kw):
    """Gedung yang sudah disurvei (terverifikasi): beton, akses 24 jam, lantai hasil survei."""
    b = {"id": "x", "kind": "sekolah", "levels": 4, "levels_src": "survey", "surveyed": True, "condition": "baik", "elev_src": "survei", "elev_method": "benchmark", "elev": 1.0,
         "area_m2": 400.0, "structure": "beton", "access": "24jam"}
    b.update(kw)
    return b


# ---------------------------------------------------------------------------
# C1 — kelayakan shelter terdaftar
# ---------------------------------------------------------------------------
class TestC1Rules(unittest.TestCase):
    P = area_builder.DEFAULT_PARAMS

    def ok(self, b, wave):
        return tsunami.registered_ok(b, tsunami.assess(b, wave, self.P))

    def test_layak(self):
        self.assertTrue(self.ok(_b(), 3.0))                      # genangan 2 m -> lantai 3, gedung 4 lantai
        self.assertTrue(self.ok(_b(elev=20.0), 3.0))             # tanah tinggi: tetap titik resmi

    def test_tidak_layak_mengikuti_eligible(self):
        for kw, why in ((dict(structure="bata"), "Struktur bukan beton/baja"),
                        (dict(structure="ringan"), "Struktur bukan beton/baja"),
                        (dict(access="tidak"), "Lantai atas tidak dapat diakses umum"),
                        (dict(levels_src="default"), "Jumlah lantai tidak diketahui")):
            b = _b(**kw)
            self.assertFalse(self.ok(b, 3.0), kw)
            self.assertEqual(tsunami.assess(b, 3.0, self.P)["reason"], why)

    def test_terlalu_rendah(self):
        self.assertFalse(self.ok(_b(levels=2), 3.0))              # perlu lantai 3

    def test_terdaftar_belum_terverifikasi_bukan_shelter_resmi(self):
        b = _b(levels_src="osm", verified=False, access=None)
        a = tsunami.assess(b, 3.0, self.P)
        self.assertTrue(a["suitable"])          # lantai cukup, TETAPI ...
        self.assertEqual(a["tier_label"], "Perlu Verifikasi")
        self.assertFalse(a["destination"])      # ... bukan tujuan rute/alokasi (kebijakan 1 Nov 2026)
        ok, why = tsunami.registered_status(b, a)
        self.assertFalse(ok)
        self.assertTrue(why.startswith("Perlu Verifikasi"))


class TestC1Engine(unittest.TestCase):
    def setUp(self):
        self.eng = Engine(synthetic.build(log=lambda *_: None))
        self.b = next(b for b in self.eng.buildings if b.get("survey_prio") and self.eng.assess(b, 3.0)["suitable"])
        self.b.update(levels_src="survey", surveyed=True, condition="baik", elev_src="survei", elev_method="benchmark", structure="beton", access="24jam", levels_est=False)

    def test_bata_dikeluarkan_dari_rute_tabel_dan_optimizer(self):
        bid = self.b["id"]
        self.assertEqual([d["id"] for d in self.eng.shelter_dests([bid], 3.0)], [bid])
        self.b["structure"] = "bata"  # hasil survei petugas
        self.assertEqual(self.eng.shelter_dests([bid], 3.0), [])
        lt = self.eng.live_tables(closures=set(), shelter_ids=[bid], points=[], alert_active=True, wave_m=3.0)
        self.assertNotIn(bid, [d.get("id") for d in lt["dests"]["aman"]])
        self.assertNotIn(bid, [d.get("id") for d in lt["dests"]["gedung"]])
        p = self.eng.plan(eta_min=20, delay_min=5, margin_min=2, shelter_ids=[bid], design_height_m=3.0,
                          optimize_k=3)
        s = p["shelters"][0]
        self.assertFalse(s["usable"])
        self.assertEqual((s["capacity"], s["assigned"]), (0, 0))
        self.assertEqual(s["reason"], "Struktur bukan beton/baja")
        self.assertNotIn(bid, [r["id"] for r in p["recommended"]])

    def test_route_tidak_mengarah_ke_shelter_bata(self):
        bid = self.b["id"]
        self.b["structure"] = "bata"
        for mode in ("aman", "gedung"):
            r = self.eng.route(self.b["lat"], self.b["lon"], mode=mode, group="umum", closures=set(),
                               shelter_ids=[bid], points=[], alert_active=True, wave_m=3.0, alternatives=8)
            self.assertNotIn(bid, [x["dest"].get("id") for x in r.get("routes", [])], mode)


class TestC1API(APITestBase):
    def test_daftar_survei_bata_lalu_batal(self):
        st = app_module.STATE
        eng = st.get(None).engine
        bid = next(b["id"] for b in eng.buildings if b.get("survey_prio") and eng.assess(b, 3.0)["suitable"])
        hp, h = self.login("perencana", "perencana123"), self.login()
        lv = next(b["levels"] for b in eng.buildings if b["id"] == bid)
        self.client.put(f"/api/buildings/{bid}", json={"levels": lv, "levels_confirmed": True, "condition": "baik", "structure": "beton", "access": "24jam"},
                        headers=h)  # shelter resmi wajib disurvei dulu
        self.assertEqual(self.client.post(f"/api/shelters/{bid}", headers=hp).status_code, 200)
        a = self.client.post("/api/alerts/simulate", json={"level": "siaga", "scope": "all"}, headers=h).json()

        def dest_ids(kind):
            return [d.get("id") for d in self.client.get("/api/tables").json()["dests"][kind]]

        self.assertIn(bid, dest_ids("aman"))
        r = self.client.put(f"/api/buildings/{bid}", json={"levels": lv, "levels_confirmed": True, "condition": "baik", "structure": "bata", "access": "24jam"},
                            headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn(bid, dest_ids("aman"))
        self.assertNotIn(bid, dest_ids("gedung"))
        sh = next(s for s in self.client.get("/api/state").json()["shelters"] if s["id"] == bid)
        self.assertFalse(sh["usable"])
        plan = self.client.post("/api/plan", json={"design_height_m": 3}, headers=hp).json()
        ps = next(s for s in plan["shelters"] if s["id"] == bid)
        self.assertFalse(ps["usable"])
        self.assertEqual(ps["assigned"], 0)
        # survei dibatalkan -> belum terverifikasi lagi: bukan shelter resmi DAN bukan tujuan rute; hanya info
        # "Perlu Verifikasi" tanpa rute (dests.unverified)
        self.client.delete(f"/api/buildings/{bid}/survey", headers=h)
        self.assertNotIn(bid, dest_ids("aman"))
        self.assertNotIn(bid, dest_ids("gedung"))
        g = next(d for d in self.client.get("/api/tables").json()["dests"]["unverified"] if d.get("id") == bid)
        self.assertEqual(g["tier_label"], "Perlu Verifikasi")
        sh = next(s for s in self.client.get("/api/state").json()["shelters"] if s["id"] == bid)
        self.assertFalse(sh["usable"])
        self.assertTrue(sh["reason"].startswith("Perlu Verifikasi"))
        # survei ulang (beton, 24 jam) -> shelter resmi lagi
        self.client.put(f"/api/buildings/{bid}", json={"levels": lv, "levels_confirmed": True, "condition": "baik", "structure": "beton", "access": "24jam"},
                        headers=h)
        self.assertIn(bid, dest_ids("aman"))
        self.client.delete(f"/api/buildings/{bid}/survey", headers=h)
        self.client.post(f"/api/alerts/{a['id']}/end", json={"note": "uji selesai"}, headers=h)
        self.client.delete(f"/api/shelters/{bid}", headers=hp)

    def test_tidak_bisa_mendaftarkan_gedung_tanpa_akses(self):
        eng = app_module.STATE.get(None).engine
        bid = next(b["id"] for b in eng.buildings if b.get("survey_prio"))
        h, hp = self.login(), self.login("perencana", "perencana123")
        self.client.put(f"/api/buildings/{bid}", json={"access": "tidak", "structure": "beton"}, headers=h)
        r = self.client.post(f"/api/shelters/{bid}", headers=hp)
        self.assertEqual(r.status_code, 400)
        self.assertIn("tidak dapat diakses", r.json()["detail"])
        self.client.delete(f"/api/buildings/{bid}/survey", headers=h)


# ---------------------------------------------------------------------------
# C2 — akun demo tidak ada / tidak bisa dipakai di server publik
# ---------------------------------------------------------------------------
class TestC2Accounts(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(config, "USERS_PATH", self.tmp / "users.json"),
                        mock.patch.object(config, "SECRET_PATH", self.tmp / "secret.key"),
                        mock.patch.object(config, "PUBLIC_URL", ""),
                        mock.patch.dict(os.environ, {}, clear=False)]
        for p in self.patches:
            p.start()
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        self.log = []

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ensure(self):
        return auth.ensure_accounts(log=self.log.append)

    def test_deteksi_server_publik(self):
        self.assertFalse(config.is_public())
        with mock.patch.object(config, "PUBLIC_URL", "http://192.168.1.5:8000"):
            self.assertFalse(config.is_public())  # LAN tetap lokal
        with mock.patch.object(config, "PUBLIC_URL", "https://kata-acak.trycloudflare.com"):
            self.assertTrue(config.is_public())   # tunnel
        with mock.patch.dict(os.environ, {"RENDER_EXTERNAL_URL": "https://sempat.onrender.com"}):
            self.assertTrue(config.is_public())
        with mock.patch.dict(os.environ, {"SEMPAT_PUBLIC": "1"}):
            self.assertTrue(config.is_public())   # Dockerfile

    def test_lokal_tetap_punya_akun_demo(self):
        self.assertEqual(self.ensure(), [])
        self.assertIsNotNone(auth.verify("petugas", "petugas123"))
        self.assertIsNotNone(auth.verify("perencana", "perencana123"))
        self.assertTrue(auth.demo_accounts_allowed())

    def test_publik_tanpa_env_tidak_membuat_akun_demo(self):
        os.environ["SEMPAT_PUBLIC"] = "1"
        gen = dict(self.ensure())
        self.assertEqual(set(gen), {"petugas", "perencana"})
        self.assertIsNone(auth.verify("petugas", "petugas123"))
        self.assertIsNone(auth.verify("perencana", "perencana123"))
        self.assertEqual(auth.verify("petugas", gen["petugas"])["role"], "petugas")
        self.assertTrue(all(not auth._matches(u, auth._DEFAULT_PW[u["username"]]) for u in auth._read()))
        self.assertFalse(auth.demo_accounts_allowed())
        self.assertEqual(self.ensure(), [])  # idempoten: tidak membuat ulang

    def test_publik_memakai_env(self):
        os.environ.update({"SEMPAT_PUBLIC": "1", "SEMPAT_PETUGAS_PASSWORD": "Rahasia-Petugas-1",
                           "SEMPAT_PERENCANA_PASSWORD": "Rahasia-Rencana-2"})
        self.assertEqual(self.ensure(), [])
        self.assertIsNotNone(auth.verify("petugas", "Rahasia-Petugas-1"))
        self.assertIsNotNone(auth.verify("perencana", "Rahasia-Rencana-2"))
        self.assertIsNone(auth.verify("petugas", "petugas123"))
        os.environ["SEMPAT_PETUGAS_PASSWORD"] = "Kata-Sandi-Baru-3"  # env diganti -> diselaraskan
        self.ensure()
        self.assertIsNone(auth.verify("petugas", "Rahasia-Petugas-1"))
        self.assertIsNotNone(auth.verify("petugas", "Kata-Sandi-Baru-3"))

    def test_env_terlalu_pendek_diabaikan(self):
        os.environ.update({"SEMPAT_PUBLIC": "1", "SEMPAT_PETUGAS_PASSWORD": "pendek"})
        gen = dict(self.ensure())
        self.assertIn("petugas", gen)
        self.assertIsNone(auth.verify("petugas", "pendek"))
        self.assertTrue(any("kurang dari" in m for m in self.log))

    def test_akun_demo_lama_diganti_dan_token_lama_gugur(self):
        self.ensure()  # dulu dijalankan lokal
        tok = auth.issue(auth.verify("petugas", "petugas123"))
        self.assertIsNotNone(auth.check(tok))
        with mock.patch.object(config, "PUBLIC_URL", "https://kata-acak.trycloudflare.com"):
            gen = dict(self.ensure())
            self.assertIn("petugas", gen)
            self.assertIsNone(auth.verify("petugas", "petugas123"))
            self.assertIsNone(auth.check(tok))  # kunci token diganti

    def test_kata_sandi_demo_ditolak_walau_tersimpan(self):
        self.ensure()  # akun demo tersimpan di users.json
        os.environ["SEMPAT_PUBLIC"] = "1"
        self.assertIsNone(auth.verify("petugas", "petugas123"))  # meski ensure_accounts belum dipanggil

    def test_set_password_minimal_8(self):
        self.ensure()
        with self.assertRaises(ValueError):
            auth.set_password("petugas", "1234567")


class TestC2PublicServer(APITestBase):
    """Server dimulai dalam mode publik: lifespan menyiapkan akun tanpa akun demo."""

    @classmethod
    def setUpClass(cls):
        cls._env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["SEMPAT_PUBLIC"] = "1"
        os.environ["SEMPAT_PETUGAS_PASSWORD"] = "Uji-Petugas-Publik-9"
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        for k, v in cls._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_login_demo_ditolak(self):
        r = self.client.post("/api/auth/login", json={"username": "petugas", "password": "petugas123"})
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/api/auth/login", json={"username": "perencana", "password": "perencana123"})
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/api/auth/login", json={"username": "petugas", "password": "Uji-Petugas-Publik-9"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(self.client.get("/api/meta").json()["demo_accounts"])
        # tanpa login, peringatan tidak bisa dikirim
        self.assertEqual(self.client.post("/api/alerts/simulate", json={"scope": "all"}).status_code, 401)


# ---------------------------------------------------------------------------
# C3 — push paralel, timeout, kegagalan per pelanggan
# ---------------------------------------------------------------------------
class TestC3Push(APITestBase):
    N = 20

    def setUp(self):
        self.st = app_module.STATE
        self.assertIsNotNone(self.st.push)
        for r in self.st.store.list_subs():
            self.st.store.delete_sub(r["endpoint"])
        self.eps = []
        for i in range(self.N):
            ep = f"https://fcm.googleapis.com/fcm/send/c3-{i:02d}"
            self.st.store.upsert_sub(ep, {"endpoint": ep, "keys": {"p256dh": "x", "auth": "y"}},
                                     -9.49 + i * 0.01, 112.51)  # makin besar i makin jauh dari pusat
            self.eps.append(ep)
        self.calls, self.lock = [], threading.Lock()

    def fake(self, delay=0.0, behaviour=None):
        def send(sub, payload, vapid, **kw):
            t0 = time.time()
            with self.lock:
                self.calls.append({"ep": sub["endpoint"], "timeout": kw.get("timeout"), "t0": t0,
                                   "thread": threading.current_thread().name})
            b = (behaviour or {}).get(sub["endpoint"])
            time.sleep(b if isinstance(b, float) else delay)
            if b == "raise":
                raise OSError("jaringan putus")
            if isinstance(b, int):
                return b, ""
            return 201, ""
        return send

    def test_paralel_dan_timeout(self):
        with mock.patch("backend.webpush.send", self.fake(delay=0.3)):
            t = time.time()
            stats = self.st.push_broadcast({"type": "alert"}, wait=True)
            elapsed = time.time() - t
        self.assertEqual(stats, {"total": self.N, "ok": self.N, "gone": 0, "failed": 0})
        self.assertLess(elapsed, 1.5, f"berurutan butuh ±{0.3 * self.N:.0f} s")
        self.assertTrue(all(c["timeout"] == config.PUSH_TIMEOUT_S for c in self.calls))
        self.assertLessEqual(config.PUSH_TIMEOUT_S, 10)
        self.assertGreater(len({c["thread"] for c in self.calls}), 1)

    def test_kegagalan_per_pelanggan_terisolasi(self):
        beh = {self.eps[3]: "raise", self.eps[5]: 410, self.eps[7]: 500}
        with mock.patch("backend.webpush.send", self.fake(behaviour=beh)):
            stats = self.st.push_broadcast({"type": "alert"}, wait=True)
        self.assertEqual(stats, {"total": self.N, "ok": self.N - 3, "gone": 1, "failed": 2})
        left = {r["endpoint"] for r in self.st.store.list_subs()}
        self.assertNotIn(self.eps[5], left)  # 410 = langganan mati -> dihapus
        self.assertIn(self.eps[7], left)     # 500 = gangguan sementara -> disimpan
        self.assertIn(self.eps[3], left)

    def test_satu_hp_lambat_tidak_menahan_lainnya(self):
        beh = {self.eps[0]: 2.0}  # pelanggan pertama (terdekat) menggantung
        with mock.patch("backend.webpush.send", self.fake(delay=0.05, behaviour=beh)):
            t = time.time()
            self.st.push_broadcast({"type": "alert"}, wait=True)
        others = [c["t0"] - t for c in self.calls if c["ep"] != self.eps[0]]
        self.assertEqual(len(others), self.N - 1)
        self.assertLess(max(others), 1.0)

    def test_urutan_terdekat_dulu_dan_radius(self):
        with mock.patch("backend.webpush.send", self.fake()), mock.patch.object(config, "PUSH_WORKERS", 1):
            stats = self.st.push_broadcast({"type": "alert"}, center=(-9.49, 112.51), radius_km=12, wait=True)
        got = [c["ep"] for c in self.calls]
        self.assertEqual(got, sorted(got))   # urutan = jarak naik
        self.assertEqual(stats["total"], len(got))
        self.assertLess(len(got), self.N)    # yang di luar 12 km tidak dikirimi

    def test_broadcast_tidak_menahan_request(self):
        with mock.patch("backend.webpush.send", self.fake(delay=0.5)):
            t = time.time()
            self.assertIsNone(self.st.push_broadcast({"type": "alert"}))
            self.assertLess(time.time() - t, 0.2)
            deadline = time.time() + 5
            while len(self.calls) < self.N and time.time() < deadline:
                time.sleep(0.05)
            time.sleep(0.6)
        self.assertEqual(len(self.calls), self.N)

    def test_peringatan_petugas_sampai_ke_semua_hp(self):
        h = self.login()
        with mock.patch("backend.webpush.send", self.fake()), \
                mock.patch.object(self.st, "push_broadcast", lambda *a, **k: app_module.AppState.push_broadcast(
                    self.st, *a, **{**k, "wait": True})):
            a = self.client.post("/api/alerts/simulate", json={"scope": "all"}, headers=h).json()
        self.assertEqual(len(self.calls), self.N)
        self.client.post(f"/api/alerts/{a['id']}/end", json={"note": "uji selesai"}, headers=h)


if __name__ == "__main__":
    unittest.main()
