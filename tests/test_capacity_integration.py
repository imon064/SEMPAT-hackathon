"""Integrasi alokasi kapasitas: mesin rute, tabel HP, API, alur peringatan (data uji SINTETIS).

Yang diuji di sini adalah KONSISTENSI dan ALUR, bukan angka lapangan:
* rute online (route) mengikuti alokasi yang sama dengan tabel HP & dasbor;
* peringatan (SSE + push) terkirim sebelum dan tanpa menunggu optimasi; status "pending" eksplisit;
* kegagalan optimasi tidak menahan peringatan; okupansi petugas mengurangi kapasitas; perencana memakai model sama.
"""
import time
import unittest
from unittest import mock

from backend import allocation as AL
from backend import app as app_module
from backend import synthetic
from backend.engine import Engine

from tests.test_api import APITestBase


VERIFIED = dict(levels_src="survey", surveyed=True, condition="baik", structure="beton", access="24jam",
                elev_src="survei", elev_method="benchmark", levels_est=False)


def synthetic_engine():
    """Desa sintetis dengan gedung bertingkat yang sudah TERVERIFIKASI (survei lengkap + elevasi terukur): sejak
    kebijakan 1 Nov 2026 hanya gedung terverifikasi yang menjadi tujuan alokasi, jadi uji kapasitas memerlukannya."""
    area = synthetic.build(log=lambda *_: None)
    for b in area["buildings"]:
        if b["kind"] != "rumah" and int(b.get("levels") or 1) >= 2:
            b.update(VERIFIED)
    return area, Engine(area)


class TestEngineConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.area, cls.eng = synthetic_engine()

    def test_rute_online_sama_dengan_alokasi(self):
        """Dari setiap bangunan berpenghuni: tujuan route() = tujuan alokasi bangunan itu (atau salah satu porsinya)."""
        eng = self.eng
        H = 3.0
        for mode in ("gedung",):
            dests, high = eng.mode_dests(mode, shelter_ids=[], H=H)
            out = eng.allocate(mode, dests, closures=set(), high=high, H=H, budget_s=eng.budget_s(None))
            self.assertIn(out["status"], ("ok", "kurang"))
            checked = split = 0
            for oi, (bi, g, cands, fb) in enumerate(out["origins"]):
                f = {d: n for d, n in out["result"]["flows"].get(oi, {}).items() if n > 0 and d != AL.UNRESOLVED}
                if not f:
                    continue
                b = eng.buildings[bi]
                for dk in (None, "hp-1", "hp-2"):
                    r = eng.route(b["lat"], b["lon"], mode=mode, group=g, closures=set(), shelter_ids=[], points=[],
                                  alert_active=True, wave_m=H, alternatives=1, device_key=dk)
                    self.assertTrue(r["ok"], r)
                    dest = r["routes"][0]["dest"]
                    got = next((i for i, d in enumerate(dests) if d["id"] == dest.get("id")), AL.UNLIMITED)
                    if "allocation" in r:
                        # Override: bangunan di titik yang PERSIS sama (mis. berhadapan di satu ruas) digabung jadi
                        # satu kumpulan porsi -> HP memilih dari kumpulan itu (HP tidak bisa membedakannya).
                        rec = next(x for x in out["overrides"][g] if x["o"] == r["allocation"]["origin"])
                        pool = {o["d"] for o in rec["opts"]}
                        self.assertTrue(set(f) <= pool, f"{b['id']}/{g}: alokasi {f} tidak ada di override {pool}")
                        self.assertIn(got, pool)
                        split += 1
                    else:
                        self.assertEqual(len(f), 1, f"{b['id']}/{g}: alokasi dipecah tanpa override")
                        self.assertEqual(got, next(iter(f)), f"{b['id']}/{g}: rute {got} != alokasi {f}")
                checked += 1
            self.assertGreater(checked, 200)

    def test_mode_gedung_selalu_menyertakan_zona_aman(self):
        """Temuan audit T5: route(mode=gedung) dulu tanpa zona aman, tabel HP dengan zona aman -> kini sama."""
        eng = self.eng
        lt = eng.live_tables(closures=set(), shelter_ids=[], points=[], alert_active=True, wave_m=3.0)
        safe_dests = 0
        for x in range(200, 2300, 150):
            for y in (300, 700, 1100):
                lat, lon = synthetic.PROJ.to_latlon(x, y)
                r = eng.route(lat, lon, mode="gedung", group="umum", closures=set(), shelter_ids=[], points=[],
                              alert_active=True, wave_m=3.0, alternatives=1)
                if not (r["ok"] and r["routes"]):
                    continue
                safe_dests += r["routes"][0]["dest"]["type"] == "safe_zone"
        self.assertGreater(safe_dests, 0, "zona aman harus bisa menjadi tujuan mode gedung")
        self.assertIn(lt["capacity"]["gedung"]["status"], ("ok", "kurang"))

    def test_tanpa_penduduk_status_eksplisit(self):
        area = synthetic.build(log=lambda *_: None)
        for b in area["buildings"]:
            b["residents"] = 0.0
        eng = Engine(area)
        lt = eng.live_tables(closures=set(), shelter_ids=[], points=[], alert_active=True, wave_m=3.0)
        for mode in ("aman", "gedung"):
            self.assertEqual(lt["capacity"][mode]["status"], "tanpa_penduduk")
            self.assertTrue(lt["capacity"][mode]["messages"])
        self.assertEqual(sum(len(v) for v in lt["overrides"].values()), 0)

    def test_skenario_berubah_tidak_memakai_hasil_lama(self):
        """Gedung tidak tersedia setelah alokasi (okupansi = kapasitas): kunci baru -> pending, bukan hasil lama."""
        eng = self.eng
        H = 3.0
        dests, high = eng.mode_dests("gedung", shelter_ids=[], H=H)
        budget = eng.budget_s(None)
        eng.allocate("gedung", dests, closures=set(), high=high, H=H, budget_s=budget)
        _, _, out, view = eng.mode_allocation("gedung", dests, high, closures=set(), H=H, budget_s=budget,
                                              alloc="cached")
        self.assertIsNotNone(out)
        busiest = max(range(len(dests)), key=lambda i: out["result"]["load"][i])
        occ = {dests[busiest]["id"]: int(dests[busiest]["capacity"])}
        d2, h2 = eng.mode_dests("gedung", shelter_ids=[], H=H, occupancy=occ)
        self.assertEqual(d2[busiest]["capacity"], 0)
        pen, ovr, out2, view2 = eng.mode_allocation("gedung", d2, h2, closures=set(), H=H, budget_s=budget,
                                                    alloc="cached")
        self.assertIsNone(out2)
        self.assertEqual(view2["status"], "pending")
        self.assertEqual(sum(len(v) for v in ovr.values()), 0)
        self.assertTrue(all(p == 0 for p in pen))
        out3 = eng.allocate("gedung", d2, closures=set(), high=h2, H=H, budget_s=budget)
        self.assertEqual(out3["result"]["load"][busiest], 0)
        self.assertEqual(AL.validate(AL.Problem([AL.Origin(f"{bi}|{g}", sum(f.values()), c, fb)
                                                 for (bi, g, c, fb), f in
                                                 ((out3["origins"][oi], out3["result"]["flows"].get(oi, {}))
                                                  for oi in range(len(out3["origins"])))], d2),
                                     out3["result"]["flows"]), [])

    def test_beban_seperti_hp_sama_dengan_alokasi(self):
        eng = self.eng
        H = 3.0
        dests, high = eng.mode_dests("gedung", shelter_ids=[], H=H)
        out = eng.allocate("gedung", dests, closures=set(), high=high, H=H, budget_s=eng.budget_s(None),
                           cache=False)
        self.assertEqual(out["table_errors"], [])
        self.assertEqual(out["table_load"], out["result"]["load"])
        for d, dd in enumerate(dests):
            self.assertLessEqual(out["table_load"][d], dd["capacity"])

    def test_perencana_memakai_model_alokasi(self):
        eng = self.eng
        p = eng.plan(eta_min=20, delay_min=5, margin_min=2, shelter_ids=[], optimize_k=3, design_height_m=3.0)
        al = p["allocation"]
        self.assertIn(al["status"], ("ok", "kurang"))
        self.assertTrue(al["method"].startswith("lp") or al["method"] == "greedy")
        self.assertEqual(al["totals"]["demand"], eng.demand_info()["people"])
        t = al["totals"]
        self.assertEqual(t["to_shelter"] + t["to_unlimited"] + t["unresolved"], t["demand"])
        self.assertLessEqual(t["capacity_used"], t["capacity_total"])
        for s in p["recommended"] + p["shelters"]:
            self.assertLessEqual(s["assigned"], s["capacity"], s["id"])


class TestAlertFlowWithAllocation(APITestBase):
    """Peringatan tidak menunggu optimasi (diperlambat buatan 2 s di sini)."""

    def setUp(self):
        # Hotel 3 lantai disurvei TERVERIFIKASI dengan elevasi terukur: tujuan gedung untuk skenario Awas (10 m).
        b = next(x for x in self.area["buildings"] if x["name"] == "Hotel Pantai Indah")
        r = self.client.put(f"/api/buildings/{b['id']}", headers=self.login(), json={
            "levels": b["levels"], "levels_confirmed": True, "condition": "baik", "structure": "beton",
            "access": "24jam", "ground_elev_m": b["elev"], "ground_elev_method": "benchmark"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["tier"], "verified")
        app_module.STATE.wait_allocations(120)

    def _end_alert(self, h):
        st = self.client.get("/api/state").json()
        if st.get("alert"):
            self.client.post(f"/api/alerts/{st['alert']['id']}/end", headers=h, json={"note": "selesai uji"})
        app_module.STATE.wait_allocations(120)

    def test_01_peringatan_tidak_menunggu_alokasi(self):
        h = self.login()
        st = app_module.STATE
        st.wait_allocations(120)
        events, t_alloc = [], {"start": [], "end": []}
        orig_publish = st.broker.publish
        orig_alloc = Engine.allocate

        def rec_publish(ev, data):
            events.append((ev, time.perf_counter(), dict(data)))
            return orig_publish(ev, data)

        def slow_alloc(self_, *a, **k):
            t_alloc["start"].append(time.perf_counter())
            time.sleep(2.0)  # optimasi lambat buatan
            try:
                return orig_alloc(self_, *a, **k)
            finally:
                t_alloc["end"].append(time.perf_counter())

        push_calls = []
        with mock.patch.object(st.broker, "publish", side_effect=rec_publish), \
                mock.patch.object(Engine, "allocate", slow_alloc), \
                mock.patch.object(st, "push_broadcast", side_effect=lambda *a, **k: push_calls.append(time.perf_counter())):
            t0 = time.perf_counter()
            r = self.client.post("/api/alerts/simulate", json={"magnitude": 7.4, "eta_min": 18}, headers=h)
            t1 = time.perf_counter()
            self.assertEqual(r.status_code, 201, r.text)
            alert_t = next(t for ev, t, _ in events if ev == "alert")
            # Peringatan & push terkirim sebelum optimasi mana pun selesai; respons tidak menunggu optimasi.
            self.assertTrue(push_calls, "push harus dijadwalkan")
            self.assertLess(alert_t - t0, 1.0)
            self.assertLess(push_calls[0] - t0, 1.0)
            self.assertLess(t1 - t0, 2.0, "request peringatan menunggu optimasi")
            self.assertTrue(all(alert_t < e for e in t_alloc["end"]) if t_alloc["end"] else True)
            tb = self.client.get("/api/tables").json()
            self.assertEqual(tb["capacity"]["gedung"]["status"], "pending")
            self.assertTrue(all(len(v) == 0 for v in tb["overrides"].values()))
            self.assertTrue(st.wait_allocations(120))
        self.assertTrue(t_alloc["end"], "alokasi harus berjalan di latar belakang")
        self.assertTrue(any(ev == "state" and d.get("reason") == "kapasitas" for ev, _, d in events),
                        "selesai alokasi harus diumumkan lewat SSE state")
        tb = self.client.get("/api/tables").json()
        cap = tb["capacity"]["gedung"]
        self.assertIn(cap["status"], ("ok", "kurang"))
        self.assertIsNotNone(cap["computed_at"])
        for d in cap["dests"]:
            self.assertLessEqual(d["allocated"], d["capacity"])
        al = self.client.get("/api/allocation?mode=gedung", headers=h).json()
        self.assertEqual(al["status"], cap["status"])
        self.assertEqual(sum(r["people"] for r in al["rows"]), cap["totals"]["demand"])
        per = {}
        for row in al["rows"]:
            if row["dest_type"] not in ("tanpa_batas_tercatat", "tidak_teralokasi"):
                per[row["dest"]] = per.get(row["dest"], 0) + row["people"]
        caps = {d["id"]: d["capacity"] for d in cap["dests"]}
        for k, v in per.items():
            self.assertLessEqual(v, caps[k])
        self.assertEqual(self.client.get("/api/allocation?mode=gedung").status_code, 401)
        self._end_alert(h)

    def test_02_alokasi_gagal_tidak_menahan_peringatan(self):
        h = self.login()
        st = app_module.STATE
        st.wait_allocations(120)
        with mock.patch.object(Engine, "allocate", side_effect=RuntimeError("solver mati")):
            r = self.client.post("/api/alerts/simulate", json={"magnitude": 7.0, "eta_min": 15}, headers=h)
            self.assertEqual(r.status_code, 201, r.text)
            self.assertTrue(st.wait_allocations(60))
        state = self.client.get("/api/state").json()
        self.assertIsNotNone(state["alert"])
        tb = self.client.get("/api/tables").json()
        self.assertEqual(tb["capacity"]["gedung"]["status"], "gagal")
        self.assertTrue(tb["capacity"]["gedung"]["messages"])
        self.assertEqual(sum(len(v) for v in tb["overrides"].values()), 0)
        self._end_alert(h)

    def test_03_okupansi_mengurangi_kapasitas(self):
        h = self.login()
        st = app_module.STATE
        r = self.client.post("/api/alerts/simulate", json={"magnitude": 7.4, "eta_min": 20}, headers=h)
        self.assertEqual(r.status_code, 201, r.text)
        st.wait_allocations(120)
        cap = self.client.get("/api/tables").json()["capacity"]["gedung"]
        d = max(cap["dests"], key=lambda x: x["allocated"])
        self.assertGreater(d["allocated"], 0)
        aid = self.client.get("/api/state").json()["area_id"]
        r = self.client.put(f"/api/shelters/{d['id']}/occupancy", json={"occupied": d["capacity"] - 2, "area_id": aid})
        self.assertIn(r.status_code, (401, 403))
        r = self.client.put(f"/api/shelters/{d['id']}/occupancy", headers=h,
                            json={"occupied": d["capacity"] - 2, "area_id": aid})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(st.wait_allocations(120))
        cap2 = self.client.get("/api/tables").json()["capacity"]["gedung"]
        d2 = next(x for x in cap2["dests"] if x["id"] == d["id"])
        self.assertEqual(d2["capacity"], 2)
        self.assertEqual(d2["occupied"], d["capacity"] - 2)
        self.assertLessEqual(d2["allocated"], 2)
        self.client.put(f"/api/shelters/{d['id']}/occupancy", headers=h, json={"occupied": 0, "area_id": aid})
        self._end_alert(h)


if __name__ == "__main__":
    unittest.main()
