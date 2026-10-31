"""Uji modul alokasi berkapasitas (backend/allocation.py).

Semua angka di sini SINTETIS (waktu tempuh, kapasitas, permintaan buatan). Keoptimalan LP dibandingkan dengan
MILP independen (scipy.optimize.milp, variabel bulat, bobot leksikografis) — bukan dengan keluaran solver itu sendiri.
"""
import math
import random
import unittest
from unittest import mock

from backend import allocation as AL
from backend.allocation import INF, UNLIMITED, UNRESOLVED, Origin, Problem


def P(origins, caps, T=None):
    """origins: [(demand, {dest: time}, fallback)] ; caps: [capacity]."""
    return Problem([Origin(f"o{i}", q, dict(c), fb) for i, (q, c, fb) in enumerate(origins)],
                   [{"id": f"s{j}", "capacity": k} for j, k in enumerate(caps)], deadline_s=T)


def invariants(tc, problem, res):
    """Invarian wajib untuk setiap hasil yang tidak berstatus gagal (diperiksa di sini, bukan hanya oleh validate)."""
    tc.assertNotEqual(res["status"], "gagal", res["errors"])
    tc.assertEqual(AL.validate(problem, res["flows"]), [])
    load = [0] * len(problem.dests)
    tot_alloc = tot_unres = 0
    for oi, o in enumerate(problem.origins):
        f = res["flows"].get(oi, {})
        s = 0
        for d, n in f.items():
            tc.assertIsInstance(n, int)
            tc.assertGreaterEqual(n, 0)
            s += n
            if n == 0:
                continue
            if d >= 0:
                tc.assertIn(d, o.cands, "tujuan tidak layak/terjangkau")
                tc.assertTrue(math.isfinite(o.cands[d]))
                load[d] += n
                tot_alloc += n
            elif d == UNLIMITED:
                tc.assertTrue(math.isfinite(o.fallback), "zona aman tak terjangkau dipakai")
                tot_alloc += n
            else:
                tc.assertEqual(d, UNRESOLVED)
                tot_unres += n
        tc.assertEqual(s, o.demand, f"{o.key}: pembukuan tidak tepat")
    for d, dd in enumerate(problem.dests):
        tc.assertLessEqual(load[d], dd["capacity"], f"{dd['id']} melebihi kapasitas")
    tc.assertEqual(load, res["load"])
    t = res["totals"]
    tc.assertEqual(t["demand"], sum(o.demand for o in problem.origins))
    tc.assertEqual(t["to_shelter"] + t["to_unlimited"] + t["unresolved"], t["demand"], "orang hilang dari pembukuan")
    tc.assertEqual(t["unresolved"], tot_unres)
    tc.assertEqual(t["to_shelter"] + t["to_unlimited"], tot_alloc)
    tc.assertEqual(sum(t["unresolved_by_reason"].values()), t["unresolved"])
    tc.assertEqual(res["status"] == "kurang", t["unresolved"] > 0)


def objective(problem, flows):
    """(tidak teralokasi, terlambat, total waktu) dihitung langsung dari alokasi."""
    s = AL.summarize(problem, flows)["totals"]
    return s["unresolved"], s["late"], s["time_sum"]


def milp_optimum(problem):
    """Optimum leksikografis bulat yang dihitung INDEPENDEN (scipy.optimize.milp, satu model berbobot)."""
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp
    T = problem.deadline_s
    cols = []  # (oi, d, time)
    for oi, o in enumerate(problem.origins):
        for d, c in o.cands.items():
            if math.isfinite(c):
                cols.append((oi, d, c))
        if math.isfinite(o.fallback):
            cols.append((oi, UNLIMITED, o.fallback))
        cols.append((oi, UNRESOLVED, 0.0))
    R = sum(o.demand for o in problem.origins)
    cmax = max([c for _, _, c in cols] + [1.0])
    w_late = R * cmax + 1.0
    w_unres = R * (w_late + cmax) + 1.0
    cost = np.array([w_unres if d == UNRESOLVED else (c + (w_late if T is not None and c > T + 1e-9 else 0.0))
                     for _, d, c in cols])
    n = len(cols)
    A_eq = np.zeros((len(problem.origins), n))
    for j, (oi, _, _) in enumerate(cols):
        A_eq[oi, j] = 1
    rows, ub = [], []
    for d, dd in enumerate(problem.dests):
        row = np.array([1.0 if cd == d else 0.0 for _, cd, _ in cols])
        if row.any():
            rows.append(row)
            ub.append(dd["capacity"])
    cons = [LinearConstraint(A_eq, [o.demand for o in problem.origins], [o.demand for o in problem.origins])]
    if rows:
        cons.append(LinearConstraint(np.array(rows), -np.inf, np.array(ub, dtype=float)))
    r = milp(cost, constraints=cons, integrality=np.ones(n), bounds=Bounds(0, np.inf))
    assert r.status == 0, r.message
    flows = {}
    for j, (oi, d, _) in enumerate(cols):
        k = int(round(r.x[j]))
        if k:
            flows.setdefault(oi, {})[d] = flows.get(oi, {}).get(d, 0) + k
    return objective(problem, flows)


class OptimalMixin:
    def assertOptimal(self, problem, res):
        u, l, t = objective(problem, res["flows"])
        mu, ml, mt = milp_optimum(problem)
        self.assertEqual(u, mu, "tidak teralokasi tidak minimal")
        if problem.deadline_s is not None:
            self.assertEqual(l, ml, "jumlah terlambat tidak minimal")
        self.assertAlmostEqual(t, mt, delta=1e-6 * max(1.0, mt), msg="total waktu tidak minimal")


@unittest.skipUnless(AL.HAVE_LP, "scipy tidak terpasang")
class TestBasicScenarios(OptimalMixin, unittest.TestCase):
    """12 skenario dasar dari spesifikasi."""

    def test_01_kapasitas_lebih_dari_permintaan(self):
        p = P([(30, {0: 100, 1: 200}, 900), (20, {0: 150, 1: 120}, 900)], [100, 100])
        r = AL.solve(p)
        invariants(self, p, r)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["totals"]["to_shelter"], 50)
        self.assertEqual(r["load"], [30, 20])  # masing-masing ke tujuan tercepatnya
        self.assertEqual(r["remaining"], [70, 80])
        self.assertTrue(r["meta"]["optimal"])
        self.assertOptimal(p, r)

    def test_02_kapasitas_sama_dengan_permintaan(self):
        p = P([(40, {0: 100, 1: 110}, 900), (60, {0: 105, 1: 300}, 900)], [60, 40])
        r = AL.solve(p)
        invariants(self, p, r)
        self.assertEqual(r["remaining"], [0, 0])
        self.assertEqual(r["totals"]["to_unlimited"], 0)
        self.assertOptimal(p, r)

    def test_03_kapasitas_tidak_cukup_tanpa_zona_aman(self):
        p = P([(80, {0: 100}, INF), (50, {0: 120, 1: 90}, INF)], [60, 30])
        r = AL.solve(p)
        invariants(self, p, r)
        self.assertEqual(r["status"], "kurang")
        self.assertEqual(r["totals"]["unresolved"], 40)
        self.assertEqual(r["totals"]["unresolved_by_reason"][AL.REASON_CAPACITY], 40)
        self.assertEqual(r["remaining"], [0, 0])
        self.assertOptimal(p, r)

    def test_04_satu_gedung_penuh(self):
        p = P([(25, {0: 50, 1: 80}, 600)], [0, 100])
        r = AL.solve(p)
        invariants(self, p, r)
        self.assertEqual(r["load"], [0, 25])

    def test_05_kapasitas_berbeda(self):
        p = P([(70, {0: 60, 1: 90, 2: 120}, 1000), (55, {0: 80, 1: 70, 2: 150}, 1000),
               (40, {1: 65, 2: 75}, 1000)], [50, 40, 200])
        r = AL.solve(p)
        invariants(self, p, r)
        self.assertEqual(r["totals"]["to_unlimited"], 0)
        self.assertOptimal(p, r)

    def test_06_gedung_terdekat_penuh(self):
        p = P([(10, {0: 30, 1: 90}, 500)], [0, 50])
        r = AL.solve(p)
        invariants(self, p, r)
        self.assertEqual(r["flows"][0], {1: 10})

    def test_07_gedung_terdekat_sisa_sedikit(self):
        p = P([(30, {0: 30, 1: 90}, 500)], [3, 50])
        r = AL.solve(p)
        invariants(self, p, r)
        self.assertEqual(r["load"], [3, 27])

    def test_08_gedung_tidak_terjangkau(self):
        # gedung 0 tidak terjangkau dari asal (tidak ada di kandidat): tidak boleh menerima siapa pun
        p = P([(20, {1: 200}, 400), (5, {0: 50}, 400)], [100, 100])
        r = AL.solve(p)
        invariants(self, p, r)
        self.assertEqual(r["flows"][0], {1: 20})
        bad = {0: {0: 20}, 1: {0: 5}}
        self.assertTrue(any("tidak layak/terjangkau" in e for e in AL.validate(p, bad)))

    def test_09_gedung_tidak_tersedia_setelah_alokasi(self):
        p = P([(40, {0: 50, 1: 70}, 600)], [40, 40])
        r = AL.solve(p)
        self.assertEqual(r["load"], [40, 0])
        # gedung 0 ditutup: hasil lama TIDAK sah untuk keadaan baru (validasi menolaknya) -> hitung ulang
        p2 = P([(40, {0: 50, 1: 70}, 600)], [0, 40])
        self.assertTrue(any("kapasitas" in e for e in AL.validate(p2, r["flows"])))
        r2 = AL.solve(p2)
        invariants(self, p2, r2)
        self.assertEqual(r2["load"], [0, 40])

    def test_10_zona_aman_satu_satunya_tujuan(self):
        p = P([(35, {0: 50, 1: 70}, 400)], [0, 0])
        r = AL.solve(p)
        invariants(self, p, r)
        self.assertEqual(r["flows"][0], {UNLIMITED: 35})
        self.assertEqual(r["status"], "ok")

    def test_11_tidak_ada_tujuan_aman(self):
        p = P([(12, {}, INF), (8, {0: 40}, INF)], [8])
        r = AL.solve(p)
        invariants(self, p, r)
        self.assertEqual(r["status"], "kurang")
        self.assertEqual(r["flows"][0], {UNRESOLVED: 12})
        self.assertEqual(r["totals"]["unresolved_by_reason"][AL.REASON_NO_DEST], 12)
        self.assertEqual(r["totals"]["unresolved_by_reason"][AL.REASON_CAPACITY], 0)

    def test_12_permintaan_nol(self):
        p = P([(0, {0: 10}, 100), (7, {0: 10}, 100)], [5])
        r = AL.solve(p)
        invariants(self, p, r)
        self.assertEqual(r["flows"].get(0, {}), {})
        self.assertEqual(r["load"], [5])
        empty = P([(0, {0: 10}, 100)], [5])
        r = AL.solve(empty)
        invariants(self, empty, r)
        self.assertEqual(r["totals"]["demand"], 0)


@unittest.skipUnless(AL.HAVE_LP, "scipy tidak terpasang")
class TestAdversarial(OptimalMixin, unittest.TestCase):
    """Contoh dari audit independen (docs/capacity/AUDIT.md) dan variasinya."""

    def test_rantai_200_orang_gedung_kapasitas_20(self):
        # Audit (implementasi lama): 170 orang masuk gedung berkapasitas 20.
        cands = {j: 60.0 + 30 * j for j in range(10)}
        p = P([(200, cands, 5000)], [20] * 10)
        r = AL.solve(p)
        invariants(self, p, r)
        self.assertEqual(r["load"], [20] * 10)
        self.assertEqual(r["totals"]["to_unlimited"], 0)
        self.assertOptimal(p, r)

    def test_dua_gedung_200_permintaan_600(self):
        # Audit (lama): A=600, B=0.
        p = P([(600, {0: 100, 1: 130}, 900)], [200, 200])
        r = AL.solve(p)
        invariants(self, p, r)
        self.assertEqual(r["load"], [200, 200])
        self.assertEqual(r["totals"]["to_unlimited"], 200)
        p2 = P([(600, {0: 100, 1: 130}, INF)], [200, 200])
        r2 = AL.solve(p2)
        invariants(self, p2, r2)
        self.assertEqual(r2["totals"]["unresolved"], 200)
        self.assertEqual(r2["status"], "kurang")

    def test_500_orang_dua_gedung_300(self):
        p = P([(500, {0: 100, 1: 140}, 2000)], [300, 300])
        r = AL.solve(p)
        invariants(self, p, r)
        self.assertEqual(r["load"], [300, 200])
        self.assertEqual(r["totals"]["to_unlimited"], 0)

    def test_gedung_dekat_masih_ada_sisa_zona_aman_jauh(self):
        # Audit (lama): 425 orang dikirim ke zona aman jauh padahal gedung dekat masih ada tempat.
        p = P([(300, {0: 90}, 1500), (200, {0: 120}, 1600)], [450])
        r = AL.solve(p)
        invariants(self, p, r)
        self.assertEqual(r["load"], [450])
        self.assertEqual(r["totals"]["to_unlimited"], 50)
        self.assertOptimal(p, r)

    def test_zona_aman_lebih_cepat_tidak_memakai_kursi_gedung(self):
        p = P([(100, {0: 400}, 150)], [500])
        r = AL.solve(p)
        invariants(self, p, r)
        self.assertEqual(r["flows"][0], {UNLIMITED: 100})

    def test_semua_gedung_penuh(self):
        p = P([(50, {0: 60, 1: 80}, 700), (30, {1: 50}, INF)], [0, 0])
        r = AL.solve(p)
        invariants(self, p, r)
        self.assertEqual(r["flows"][0], {UNLIMITED: 50})
        self.assertEqual(r["flows"][1], {UNRESOLVED: 30})
        self.assertEqual(r["totals"]["unresolved_by_reason"][AL.REASON_CAPACITY], 30)

    def test_kapasitas_total_tidak_cukup(self):
        p = P([(100, {0: 50, 1: 60}, INF), (100, {0: 70, 1: 40}, INF), (100, {1: 90}, 2000)], [80, 90])
        r = AL.solve(p)
        invariants(self, p, r)
        # asal 2 punya zona aman: kursi gedung untuk asal 0/1 yang tidak punya alternatif (min tidak teralokasi)
        self.assertEqual(r["totals"]["unresolved"], 200 - 170)
        self.assertEqual(r["flows"][2], {UNLIMITED: 100})
        self.assertOptimal(p, r)

    def test_banyak_asal_berebut_batas_waktu(self):
        # A hanya sempat (<= T) ke gedung 0; B sempat ke gedung 0 dan zona aman. Gedung 0 = untuk A.
        T = 300
        p = P([(40, {0: 290}, 320), (40, {0: 100}, 280)], [40], T=T)
        r = AL.solve(p)
        invariants(self, p, r)
        self.assertEqual(r["flows"][0], {0: 40})
        self.assertEqual(r["flows"][1], {UNLIMITED: 40})
        self.assertEqual(r["totals"]["late"], 0)
        self.assertOptimal(p, r)
        # Tanpa tahap batas waktu, total waktu minimum (420·40 < 570·40) memberi gedung ke B -> 40 orang A terlambat.
        r2 = AL.solve(p, prioritize_deadline=False)
        invariants(self, p, r2)
        self.assertEqual(r2["totals"]["late"], 40)

    def test_distribusi_penduduk_berbeda_graf_sama(self):
        rng = random.Random(7)
        times = [[rng.uniform(60, 900) if rng.random() < 0.7 else None for _ in range(6)] for _ in range(25)]
        fb = [rng.choice([rng.uniform(300, 1500), INF]) for _ in range(25)]
        caps = [rng.randint(0, 60) for _ in range(6)]
        for trial in range(12):
            dem = [rng.choice([0, rng.randint(1, 40)]) for _ in range(25)]
            T = rng.choice([None, 400.0, 700.0])
            p = P([(dem[i], {j: t for j, t in enumerate(times[i]) if t is not None}, fb[i]) for i in range(25)],
                  caps, T=T)
            r = AL.solve(p)
            invariants(self, p, r)
            self.assertOptimal(p, r)


class TestInvariantsRandom(OptimalMixin, unittest.TestCase):
    """Uji acak: invarian untuk LP DAN greedy; keoptimalan untuk LP (bila scipy ada)."""

    def gen(self, rng, n_o, n_d):
        origins = []
        for _ in range(n_o):
            c = {d: round(rng.uniform(30, 1200), 1) for d in range(n_d) if rng.random() < 0.5}
            fb = rng.choice([INF, round(rng.uniform(200, 2500), 1)])
            origins.append((rng.randint(0, 50), c, fb))
        caps = [rng.choice([0, rng.randint(1, 80), rng.randint(80, 400)]) for _ in range(n_d)]
        return P(origins, caps, T=rng.choice([None, 600.0]))

    def test_acak(self):
        rng = random.Random(2026)
        for _ in range(40):
            p = self.gen(rng, rng.randint(1, 30), rng.randint(1, 8))
            g = AL.solve(p, use_lp=False)
            invariants(self, p, g)
            self.assertEqual(g["meta"]["method"] in ("greedy", "kosong"), True)
            if AL.HAVE_LP:
                r = AL.solve(p)
                invariants(self, p, r)
                self.assertOptimal(p, r)
                # LP tidak pernah lebih buruk dari greedy (leksikografis)
                self.assertLessEqual(objective(p, r["flows"])[:2], objective(p, g["flows"])[:2])


class TestFallbackAndValidation(unittest.TestCase):
    def test_tanpa_scipy_greedy_dilaporkan(self):
        p = P([(200, {j: 60.0 + 30 * j for j in range(10)}, 5000)], [20] * 10)
        with mock.patch.object(AL, "HAVE_LP", False):
            r = AL.solve(p)
        invariants(self, p, r)
        self.assertEqual(r["meta"]["method"], "greedy")
        self.assertFalse(r["meta"]["optimal"])
        self.assertTrue(any("scipy" in m for m in r["meta"]["messages"]))

    @unittest.skipUnless(AL.HAVE_LP, "scipy tidak terpasang")
    def test_solver_gagal_memakai_greedy(self):
        p = P([(600, {0: 100, 1: 130}, 900)], [200, 200])
        with mock.patch.object(AL, "_solve_lp", side_effect=RuntimeError("batas waktu")):
            r = AL.solve(p)
        invariants(self, p, r)
        self.assertEqual(r["meta"]["method"], "greedy")
        self.assertFalse(r["meta"]["optimal"])
        self.assertTrue(any("LP gagal" in m for m in r["meta"]["messages"]))

    def test_hasil_tidak_sah_berstatus_gagal(self):
        p = P([(10, {0: 50}, 300)], [5])
        bad_flows = {0: {0: 10}}
        with mock.patch.object(AL, "greedy", return_value=bad_flows):
            r = AL.solve(p, use_lp=False)
        self.assertEqual(r["status"], "gagal")
        self.assertTrue(any("kapasitas" in e for e in r["errors"]))

    def test_validasi_menangkap_semua_pelanggaran(self):
        p = P([(10, {0: 50}, INF), (4, {0: 30}, 100)], [6])
        cases = {
            "kapasitas": {0: {0: 10}, 1: {0: 4}},
            "permintaan": {0: {0: 3}, 1: {UNLIMITED: 4}},           # 7 orang hilang
            "tidak bulat": {0: {0: 5.5, UNRESOLVED: 4.5}, 1: {UNLIMITED: 4}},
            "negatif": {0: {0: 12, UNRESOLVED: -2}, 1: {UNLIMITED: 4}},
            "zona aman yang tidak terjangkau": {0: {UNLIMITED: 10}, 1: {UNLIMITED: 4}},
            "tidak dikenal": {0: {7: 10}, 1: {UNLIMITED: 4}},
        }
        for word, fl in cases.items():
            errs = AL.validate(p, fl)
            self.assertTrue(any(word in e for e in errs), (word, errs))

    def test_largest_remainder(self):
        vals = [3.4, 2.6, 0.5, 0.5, 7.0]
        out = AL.largest_remainder(vals)
        self.assertEqual(sum(out), round(sum(vals)))
        self.assertTrue(all(abs(a - b) < 1 for a, b in zip(out, vals)))
        self.assertEqual(AL.largest_remainder([0.2] * 10), [1, 1, 0, 0, 0, 0, 0, 0, 0, 0])

    def test_pick_option_proporsional_dan_stabil(self):
        counts = [30, 10, 60]
        hits = [0, 0, 0]
        for i in range(6000):
            hits[AL.pick_option(f"hp{i}", "b1", counts)] += 1
        for h, c in zip(hits, counts):
            self.assertAlmostEqual(h / 6000, c / 100, delta=0.03)
        self.assertEqual(AL.pick_option("x", "b1", counts), AL.pick_option("x", "b1", counts))
        self.assertEqual(AL.pick_option("x", "b1", [0, 0]), 0)
        self.assertEqual(AL.pick_option("x", "b1", [0, 5]), 1)


@unittest.skipUnless(AL.HAVE_LP, "scipy tidak terpasang")
class TestReconciliation(unittest.TestCase):
    def test_pecahan_dibulatkan_tanpa_melanggar(self):
        """Paksa solusi LP pecahan: hasil akhir tetap bulat, kapasitas & pembukuan tepat."""
        import numpy as np
        p = P([(3, {0: 10, 1: 12}, 100), (3, {0: 11, 1: 10}, 100), (5, {0: 9, 1: 9.5}, 100)], [4, 4])
        lp = AL._LP(p, [o.demand for o in p.origins], [4, 4])
        rng = np.random.default_rng(1)
        x = np.zeros(len(lp.c))
        for oi, o in enumerate(p.origins):  # bagi permintaan secara pecahan ke semua kolom asal
            js = [j for j in range(len(lp.c)) if lp.oi[j] == oi and not lp.is_z[j]]
            w = rng.random(len(js))
            x[js] = o.demand * w / w.sum()
        flows, info = AL._round_flows(p, lp, x, [4, 4])
        self.assertGreater(info["fractional_vars"], 0)
        self.assertEqual(AL.validate(p, flows), [])
        tot = AL.summarize(p, flows)["totals"]
        self.assertEqual(tot["to_shelter"] + tot["to_unlimited"] + tot["unresolved"], 11)


if __name__ == "__main__":
    unittest.main()
