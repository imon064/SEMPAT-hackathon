"""Regresi Milestone 4: pembaruan area inkremental (revisi + tambalan), serialisasi malas.

Data uji SINTETIS.
"""
import json
import unittest

from backend import app as app_module

from tests.test_api import APITestBase


SURVEY = {"levels": 4, "levels_confirmed": True, "structure": "beton", "access": "24jam", "condition": "baik"}


class TestAreaPatch(APITestBase):
    def ctx(self):
        return app_module.STATE.get(self.area["meta"]["id"])

    def tall_id(self):
        c = self.ctx()
        return next(b["id"] for b in c.engine.buildings if (b.get("levels") or 1) >= 3 and not b.get("virtual"))

    def house_id(self):
        c = self.ctx()
        return next(b["id"] for b in c.engine.buildings
                    if b.get("kind") == "rumah" and (b.get("levels") or 1) == 1 and not b.get("surveyed"))

    def meta(self, h=None):
        return self.client.get("/api/area", headers=h or {}).json()["meta"]

    def test_revisi_dan_tambalan_staf(self):
        h = self.login()
        m0 = self.meta(h)
        self.assertIn("rev", m0)
        self.assertTrue(m0["epoch"])
        bid = self.tall_id()
        r = self.client.put(f"/api/buildings/{bid}", headers=h, json=SURVEY)
        self.assertEqual(r.status_code, 200, r.text)
        p = self.client.get(f"/api/area/patch?since={m0['rev']}&epoch={m0['epoch']}", headers=h).json()
        self.assertEqual(p["rev"], m0["rev"] + 1)
        self.assertEqual([b["id"] for b in p["buildings"]], [bid])
        self.assertEqual(p["buildings"][0]["levels"], 4)
        self.assertTrue(p["buildings"][0]["surveyed"])
        self.assertNotIn("_auto", p["buildings"][0])  # versi staf: tanpa nilai internal
        # tambalan sama dengan isi /api/area penuh untuk gedung itu
        full = self.client.get("/api/area", headers=h).json()
        fb = next(b for b in full["buildings"] if b["id"] == bid)
        self.assertEqual(fb, p["buildings"][0])
        self.assertEqual(full["meta"]["rev"], p["rev"])
        # tidak ada perubahan sejak revisi terbaru -> tambalan kosong
        p2 = self.client.get(f"/api/area/patch?since={p['rev']}&epoch={m0['epoch']}", headers=h).json()
        self.assertEqual((p2["buildings"], p2["removed"]), ([], []))

    def test_tambalan_publik_ringkas_dan_penghapusan(self):
        h = self.login()
        bid = self.house_id()
        m0 = self.meta()
        self.assertNotIn(bid, [b["id"] for b in self.client.get("/api/area").json()["buildings"]])
        self.client.put(f"/api/buildings/{bid}", headers=h, json=dict(SURVEY, levels=2))
        p = self.client.get(f"/api/area/patch?since={m0['rev']}&epoch={m0['epoch']}").json()
        b = next(x for x in p["buildings"] if x["id"] == bid)  # rumah tersurvei kini relevan untuk warga
        self.assertNotIn("residents", b)
        self.assertNotIn("survey", b)  # nama petugas & catatan tidak untuk publik
        m1 = self.meta()
        self.client.delete(f"/api/buildings/{bid}/survey?area={self.area['meta']['id']}", headers=h)
        p = self.client.get(f"/api/area/patch?since={m1['rev']}&epoch={m1['epoch']}").json()
        self.assertEqual(p["removed"], [bid])  # rumah satu lantai tanpa survei: keluar lagi dari data warga

    def test_409_bila_tidak_bisa_dipastikan(self):
        m = self.meta()
        self.assertEqual(self.client.get(f"/api/area/patch?since={m['rev']}&epoch=salah").status_code, 409)
        self.assertEqual(self.client.get(f"/api/area/patch?since={m['rev'] + 5}&epoch={m['epoch']}").status_code, 409)
        c = self.ctx()
        with c.lock:
            c.refresh_bytes()  # perubahan menyeluruh (mis. peta bahaya resmi)
        self.assertEqual(self.client.get(f"/api/area/patch?since={m['rev']}&epoch={m['epoch']}").status_code, 409)

    def test_event_sse_membawa_revisi(self):
        h = self.login()
        got = []
        st = app_module.STATE
        orig = st.broker.publish
        st.broker.publish = lambda ev, data: (got.append((ev, data)), orig(ev, data))[1]
        try:
            bid = self.tall_id()
            self.client.put(f"/api/buildings/{bid}", headers=h, json=SURVEY)
        finally:
            st.broker.publish = orig
        area_ev = [d for e, d in got if e == "area"]
        self.assertEqual(len(area_ev), 1)
        self.assertEqual(area_ev[0]["ids"], [bid])
        self.assertEqual(area_ev[0]["rev"], self.ctx().rev)
        self.assertEqual(area_ev[0]["epoch"], self.ctx().epoch)


class TestEvidencePack(APITestBase):
    def url(self, q=""):
        return f"/api/areas/{self.area['meta']['id']}/evidence{q}"

    def test_hanya_perencana(self):
        self.assertEqual(self.client.get(self.url()).status_code, 401)
        self.assertEqual(self.client.get(self.url(), headers=self.login()).status_code, 403)

    def test_isi_jujur_untuk_area_sintetis(self):
        h = self.login("perencana", "perencana123")
        d = self.client.get(self.url("?eta_min=25&wave_m=8"), headers=h).json()
        codes = [x["code"] for x in d["indicators"]]
        self.assertEqual(len(codes), 12)
        self.assertEqual((codes[0], codes[-1]), ("ASSESS-1", "RESP-4"))
        self.assertTrue(d["area"]["synthetic"])
        st = {x["code"]: x["status"] for x in d["indicators"]}
        self.assertNotIn("bukti pendukung tersedia", st.values())  # sintetis tidak pernah jadi "bukti"
        for k in ("PREP-2", "PREP-4", "RESP-3", "RESP-4"):
            self.assertEqual(st[k], "di luar cakupan SEMPAT")
        self.assertIn("BUKAN penilaian", d["disclaimer"])
        self.assertIn("bukan sistem peringatan dini resmi", d["disclaimer"])
        self.assertEqual(d["scenario"]["eta_min"], 25)
        self.assertEqual(d["scenario"]["wave_m"], 8)
        self.assertEqual(d["scenario"]["budget_s"], (25 - 5 - 2) * 60)
        for k in ("sempat", "schema", "params_sha"):
            self.assertTrue(d["model"][k])
        pop = d["population"]
        self.assertLessEqual(sum(pop["in_hazard"].values()), sum(pop["total"].values()))
        # sebaran waktu + tanpa rute = total penduduk per kelompok
        for g in ("umum", "rentan"):
            self.assertEqual(sum(d["time_bands"]["people"][g].values()) + d["time_bands"]["no_route"][g], pop["total"][g])
        # tujuan = hanya gedung terverifikasi
        tiers = {b["id"]: b.get("tier") for b in self.ctx_buildings()}
        self.assertTrue(all(tiers[x["id"]] == "verified" for x in d["buildings"]["destinations"]))

    def test_bukti_berubah_setelah_verifikasi_dan_tercatat(self):
        hp = self.login()
        c = app_module.STATE.get(self.area["meta"]["id"])
        tall = next(b for b in c.engine.buildings if (b.get("levels") or 1) >= 3 and not b.get("virtual"))
        self.client.put(f"/api/buildings/{tall['id']}", headers=hp, json=dict(
            SURVEY, levels=tall["levels"], ground_elev_m=1.0, ground_elev_method="benchmark"))
        h = self.login("perencana", "perencana123")
        d = self.client.get(self.url("?wave_m=3"), headers=h).json()
        self.assertIn(tall["id"], [x["id"] for x in d["buildings"]["destinations"]])
        self.assertGreaterEqual(d["activity"]["survey_count"], 1)

    def ctx_buildings(self):
        return app_module.STATE.get(self.area["meta"]["id"]).engine.buildings


class TestLazyBytes(APITestBase):
    def test_serialisasi_hanya_saat_diminta_dan_disimpan(self):
        c = app_module.STATE.get(self.area["meta"]["id"])
        with c.lock:
            c.refresh_bytes(changed=["x"])
        self.assertEqual(c._bytes, {})
        a = c.public_bytes
        self.assertIs(a, c.public_bytes)  # disimpan, tidak dibangun ulang
        self.assertEqual(json.loads(a)["meta"]["rev"], c.rev)
        self.assertNotIn("staff", c._bytes)


if __name__ == "__main__":
    unittest.main()
