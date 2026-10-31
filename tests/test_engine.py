import math
import random
import unittest

from backend import synthetic
from backend.engine import INF, Engine, forward_k_nearest, reverse_multi_source
from backend.geo import haversine_m
from backend.speed import edge_time_s, tobler_factor


def random_graph(rng, n, m):
    edges = []
    for _ in range(m):
        u, v = rng.randrange(n), rng.randrange(n)
        if u != v:
            edges.append((u, v, rng.uniform(1, 50), rng.uniform(1, 50)))
    adj = [[] for _ in range(n)]
    radj = [[] for _ in range(n)]
    for e, (u, v, cuv, cvu) in enumerate(edges):
        adj[u].append((v, cuv, e))
        adj[v].append((u, cvu, e))
        radj[v].append((u, cuv, e))
        radj[u].append((v, cvu, e))
    return edges, adj, radj


def bellman_ford_to_targets(n, edges, sources, blocked_edges=frozenset(), blocked_nodes=frozenset()):
    """dist[x] = min_s (biaya x -> s) + biaya awal s, dihitung brute force."""
    dist = [INF] * n
    for s, (c0, _) in sources.items():
        if s not in blocked_nodes:
            dist[s] = min(dist[s], c0)
    for _ in range(n):
        changed = False
        for e, (u, v, cuv, cvu) in enumerate(edges):
            if e in blocked_edges or u in blocked_nodes or v in blocked_nodes:
                continue
            if dist[v] + cuv < dist[u]:
                dist[u] = dist[v] + cuv
                changed = True
            if dist[u] + cvu < dist[v]:
                dist[v] = dist[u] + cvu
                changed = True
        if not changed:
            break
    return dist


class TestAlgorithms(unittest.TestCase):
    def test_reverse_multi_source_matches_bruteforce(self):
        rng = random.Random(1)
        for trial in range(60):
            n = rng.randint(2, 40)
            edges, adj, radj = random_graph(rng, n, rng.randint(1, 120))
            k = rng.randint(1, max(1, n // 3))
            sources = {s: (rng.uniform(0, 20), i) for i, s in enumerate(rng.sample(range(n), k))}
            be = {rng.randrange(len(edges))} if edges and rng.random() < 0.5 else set()
            bn = {rng.randrange(n)} if rng.random() < 0.3 else set()
            dist, nxt, dest = reverse_multi_source(radj, sources, blocked_edges=be, blocked_nodes=bn)
            ref = bellman_ford_to_targets(n, edges, sources, be, bn)
            for x in range(n):
                if math.isinf(ref[x]):
                    self.assertTrue(math.isinf(dist[x]))
                else:
                    self.assertAlmostEqual(dist[x], ref[x], places=6)
                    # Mengikuti pointer next harus menghasilkan biaya yang sama
                    y, acc, steps = x, 0.0, 0
                    while nxt[y] != -1:
                        z = nxt[y]
                        acc += min(c for (w, c, e) in adj[y] if w == z and e not in be)
                        y = z
                        steps += 1
                        self.assertLess(steps, n + 1)
                    self.assertIn(y, sources)
                    self.assertAlmostEqual(acc + sources[y][0], dist[x], places=6)
                    self.assertEqual(dest[x], sources[y][1])

    def test_forward_k_nearest_matches_bruteforce(self):
        rng = random.Random(2)
        for trial in range(60):
            n = rng.randint(2, 35)
            edges, adj, radj = random_graph(rng, n, rng.randint(1, 100))
            start = rng.randrange(n)
            tnodes = rng.sample(range(n), rng.randint(1, max(1, n // 2)))
            targets = {}
            for i, t in enumerate(tnodes):
                targets.setdefault(t, []).append((i, rng.uniform(0, 10)))
            k = rng.randint(1, 4)
            got = forward_k_nearest(adj, [(start, 0.0)], targets, k)
            # brute force: jarak maju dari start
            # Graf terbalik: biaya u->v diganti biaya asli v->u, sehingga "jarak ke start" = jarak dari start.
            dist = bellman_ford_to_targets(n, [(u, v, cvu, cuv) for (u, v, cuv, cvu) in edges],
                                           {start: (0.0, 0)})
            ref = sorted(((dist[node] + extra, did) for node, lst in targets.items() for did, extra in lst
                          if math.isfinite(dist[node])))
            best = {}
            for tot, did in ref:
                best.setdefault(did, tot)
            ref_k = sorted(best.values())[:k]
            self.assertEqual(len(got), len(ref_k))
            for (did, tot, path), r in zip(got, ref_k):
                self.assertAlmostEqual(tot, r, places=6)
                self.assertEqual(path[0], start)


class TestSpeed(unittest.TestCase):
    def test_tobler(self):
        self.assertAlmostEqual(tobler_factor(0.0), 1.0, places=9)
        self.assertGreater(tobler_factor(-0.05), 1.0)        # sedikit menurun paling cepat
        self.assertLess(tobler_factor(0.2), tobler_factor(0.0))
        self.assertLess(tobler_factor(0.2), tobler_factor(-0.2))  # menanjak lebih lambat

    def test_edge_time(self):
        self.assertAlmostEqual(edge_time_s(100, 0, "umum"), 100.0, places=6)
        self.assertAlmostEqual(edge_time_s(100, 0, "rentan"), 200.0, places=6)
        self.assertGreater(edge_time_s(100, 10, "umum"), edge_time_s(100, -10, "umum"))


class TestSyntheticArea(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.area = synthetic.build(log=lambda *_: None)
        cls.eng = Engine(cls.area)

    def test_route_from_coast_reaches_safe_zone(self):
        lat, lon = synthetic.PROJ.to_latlon(300, 260)
        r = self.eng.route(lat, lon, mode="aman", group="umum", closures=set(), shelter_ids=[], points=[],
                           alert_active=False)
        self.assertTrue(r["ok"])
        rt = r["routes"][0]
        self.assertEqual(rt["dest"]["type"], "safe_zone")
        self.assertGreaterEqual(rt["dest"]["elev"], self.area["params"]["hazard_elev_threshold_m"])
        # Waktu tempuh tidak boleh lebih cepat dari jarak garis lurus / kecepatan maksimum Tobler
        straight = haversine_m(lat, lon, rt["dest"]["lat"], rt["dest"]["lon"])
        self.assertGreater(rt["time_s"], straight / (1.0 * tobler_factor(-0.05)) - 1)
        # Rentan lebih lambat
        r2 = self.eng.route(lat, lon, mode="aman", group="rentan", closures=set(), shelter_ids=[], points=[],
                            alert_active=False)
        self.assertGreater(r2["routes"][0]["time_s"], rt["time_s"])

    def test_table_consistent_with_route(self):
        lt = self.eng.live_tables(closures=set(), shelter_ids=[], points=[], alert_active=False)
        tab = lt["tables"]["aman-umum"]
        lat, lon = synthetic.PROJ.to_latlon(700, 700)
        r = self.eng.route(lat, lon, mode="aman", group="umum", closures=set(), shelter_ids=[], points=[],
                           alert_active=False)
        s = self.eng.snap(lat, lon)
        best = min(c + tab["time"][node] for node, c in self.eng.start_options(s, "umum") if tab["time"][node] >= 0)
        self.assertAlmostEqual(best, r["routes"][0]["time_s"], delta=1.5)

    def test_closure_forces_detour(self):
        # Tutup Jembatan Desa (y=500): warga timur dekat jembatan harus memutar
        lat, lon = synthetic.PROJ.to_latlon(1000, 520)
        base = self.eng.route(lat, lon, mode="aman", group="umum", closures=set(), shelter_ids=[],
                              points=[], alert_active=False)["routes"][0]["time_s"]
        def xy(i):
            return synthetic.PROJ.to_xy(self.eng.lat[i], self.eng.lon[i])
        bridge = [e for e in range(self.eng.m)
                  if abs(xy(self.eng.eu[e])[1] - 500) < 20 and abs(xy(self.eng.ev[e])[1] - 500) < 20
                  and 1035 <= xy(self.eng.eu[e])[0] <= 1165 and 1035 <= xy(self.eng.ev[e])[0] <= 1165]
        self.assertTrue(bridge)
        closed = self.eng.route(lat, lon, mode="aman", group="umum", closures=set(bridge), shelter_ids=[],
                                points=[], alert_active=False)["routes"][0]["time_s"]
        self.assertGreater(closed, base + 30)

    def test_posko_avoids_hazard_during_alert(self):
        pts = [dict(p, id=f"p{i}", updated_at="x") for i, p in enumerate(synthetic.seed_points())]
        # tambahkan titik di zona bahaya (dekat pantai)
        la, lo = synthetic.PROJ.to_latlon(600, 300)
        pts.append({"id": "pz", "kind": "posko_sar", "name": "Posko Pantai", "lat": la, "lon": lo,
                    "status": "kosong", "services": [], "updated_at": "x"})
        lat, lon = synthetic.PROJ.to_latlon(600, 1600)  # di zona aman utara
        r = self.eng.route(lat, lon, mode="posko", group="umum", closures=set(), shelter_ids=[], points=pts,
                           alert_active=True)
        self.assertTrue(r["ok"])
        names = [x["dest"]["name"] for x in r["routes"]]
        self.assertNotIn("Posko Pantai", names)
        for rt in r["routes"]:
            for lo_, la_ in rt["coords"][2:-1]:
                s = self.eng.snap(la_, lo_)
                node = s["u"] if s["t"] < 0.5 else s["v"]
                self.assertEqual(self.eng.hazard[node], 0)
        # setelah peringatan berakhir, posko pantai boleh ditawarkan
        r2 = self.eng.route(lat, lon, mode="posko", group="umum", closures=set(), shelter_ids=[], points=pts,
                            alert_active=False, alternatives=5)
        self.assertIn("Posko Pantai", [x["dest"]["name"] for x in r2["routes"]])

    def test_plan_optimizer_reduces_red(self):
        res = self.eng.plan(eta_min=20, delay_min=5, margin_min=2, shelter_ids=[], optimize_k=3)
        before = sum(res["red_before"].values())
        after = sum(res["red_after"].values())
        self.assertGreater(before, 0)
        self.assertLessEqual(after, before)
        for s in res["recommended"]:
            self.assertLessEqual(s["assigned"], s["capacity"])
        # kurva tidak naik
        reds = [c["red_total"] for c in res["curve"]]
        self.assertEqual(reds, sorted(reds, reverse=True))


if __name__ == "__main__":
    unittest.main()
