"""Engine routing dan perencanaan SEMPAT.

Metode:
- Graf jalan kaki berarah dengan biaya waktu asimetris (fungsi Tobler: menanjak
  lebih lambat daripada menurun) per kelompok warga.
- Dijkstra multi-sumber pada graf terbalik: satu kali hitung memberi waktu
  tercepat dari SETIAP node ke tujuan terdekat beserta "langkah berikutnya".
  Hasilnya dikirim ke HP sehingga rute dapat dicari tanpa internet.
- Snapping posisi GPS ke ruas jalan terdekat (proyeksi tegak lurus), bukan ke
  node terdekat, agar titik awal rute akurat.
- Perencanaan: alokasi warga ke shelter vertikal berkapasitas dan pemilihan
  shelter dengan greedy maximum coverage.
"""
from __future__ import annotations

import heapq
import math
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import tsunami
from .geo import LocalProjection, SegmentGrid, haversine_m
from .speed import GROUPS, GROUP_SPEED_MPS, edge_time_s, flat_time_s

INF = float("inf")
HIGH_GROUND = -10 ** 9  # id tujuan gabungan "tanah lebih tinggi" (mode gedung)
OPEN_POINT_STATUSES = ("kosong", "agak_ramai", "ramai", "osm")  # "osm" = fasilitas dari peta, belum diverifikasi
POINT_STATUSES = ("tutup", "kosong", "agak_ramai", "ramai", "penuh")

Adjacency = List[List[Tuple[int, float, int]]]


# ---------------------------------------------------------------------------
# Algoritma inti (fungsi murni, diuji terhadap brute force)
# ---------------------------------------------------------------------------

def reverse_multi_source(radj: Adjacency, sources: Dict[int, Tuple[float, int]],
                         blocked_edges: Optional[Set[int]] = None,
                         blocked_nodes: Optional[Set[int]] = None,
                         cutoff: float = INF) -> Tuple[List[float], List[int], List[int]]:
    """Dijkstra multi-sumber pada graf terbalik.

    radj[y] berisi (x, biaya x->y, id ruas). sources: node -> (biaya awal, id tujuan).
    Mengembalikan (dist, next, dest): dist[x] waktu tercepat dari x ke tujuan,
    next[x] node berikutnya di jalur (-1 bila x sendiri tujuan), dest[x] id tujuan.
    """
    n = len(radj)
    dist = [INF] * n
    nxt = [-1] * n
    dest = [-9] * n
    be = blocked_edges or set()
    bn = blocked_nodes or set()
    heap: List[Tuple[float, int]] = []
    for node, (c0, did) in sources.items():
        if node in bn:
            continue
        if c0 < dist[node]:
            dist[node] = c0
            dest[node] = did
            nxt[node] = -1
            heapq.heappush(heap, (c0, node))
    while heap:
        d, y = heapq.heappop(heap)
        if d > dist[y]:
            continue
        if d > cutoff:
            break
        for x, c, e in radj[y]:
            if e in be or x in bn:
                continue
            nd = d + c
            if nd < dist[x]:
                dist[x] = nd
                nxt[x] = y
                dest[x] = dest[y]
                heapq.heappush(heap, (nd, x))
    return dist, nxt, dest


def forward_k_nearest(adj: Adjacency, starts: Sequence[Tuple[int, float]],
                      targets: Dict[int, List[Tuple[int, float]]], k: int,
                      blocked_edges: Optional[Set[int]] = None,
                      blocked_nodes: Optional[Set[int]] = None) -> List[Tuple[int, float, List[int]]]:
    """Cari k tujuan berbeda dengan total waktu terkecil dari titik awal.

    targets: node -> [(id tujuan, biaya tambahan), ...].
    Mengembalikan [(id tujuan, total waktu, jalur node)], terurut naik.
    """
    n = len(adj)
    dist = [INF] * n
    prev = [-1] * n
    be = blocked_edges or set()
    bn = blocked_nodes or set()
    heap: List[Tuple[float, int]] = []
    for node, c0 in starts:
        if node in bn:
            continue
        if c0 < dist[node]:
            dist[node] = c0
            prev[node] = -1
            heapq.heappush(heap, (c0, node))
    best: Dict[int, Tuple[float, int]] = {}  # tujuan -> (total, node akhir)

    def kth_best() -> float:
        if len(best) < k:
            return INF
        return sorted(v[0] for v in best.values())[k - 1]

    while heap:
        d, u = heapq.heappop(heap)
        if d > dist[u]:
            continue
        if d > kth_best():
            break
        for did, extra in targets.get(u, ()):
            tot = d + extra
            if did not in best or tot < best[did][0]:
                best[did] = (tot, u)
        for v, c, e in adj[u]:
            if e in be or v in bn:
                continue
            nd = d + c
            if nd < dist[v]:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))
    out = []
    for did, (tot, end) in sorted(best.items(), key=lambda kv: kv[1][0])[:k]:
        path = []
        x = end
        while x != -1:
            path.append(x)
            x = prev[x]
        path.reverse()
        out.append((did, tot, path))
    return out


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class Engine:
    def __init__(self, area: dict):
        self.area = area
        nodes, edges = area["nodes"], area["edges"]
        self.lat: List[float] = nodes["lat"]
        self.lon: List[float] = nodes["lon"]
        self.elev: List[float] = nodes["elev"]
        self.hazard: List[int] = nodes["hazard"]
        self.safe: List[int] = nodes["safe"]
        self.n = len(self.lat)
        self.eu: List[int] = edges["u"]
        self.ev: List[int] = edges["v"]
        self.elen: List[float] = edges["len"]
        self.ekind: List[str] = edges["kind"]
        self.m = len(self.eu)
        self.eflag: List[str] = edges.get("flag") or [""] * self.m
        self.coast: List[float] = nodes.get("coast") or [1e9] * self.n
        self.params = area.get("params", {})
        self.design_wave_m = float(self.params.get("tsunami_design_height_m", 10.0))
        self.buildings: List[dict] = area.get("buildings", [])
        self.bindex = {b["id"]: i for i, b in enumerate(self.buildings)}
        self.proj = LocalProjection(*reversed(area["meta"]["center"]))

        # Biaya berarah per kelompok
        self.cost_uv: Dict[str, List[float]] = {}
        self.cost_vu: Dict[str, List[float]] = {}
        self.adj: Dict[str, Adjacency] = {}
        self.radj: Dict[str, Adjacency] = {}
        for g in GROUPS:
            cuv, cvu = [], []
            adj: Adjacency = [[] for _ in range(self.n)]
            radj: Adjacency = [[] for _ in range(self.n)]
            for e in range(self.m):
                u, v, L, kind = self.eu[e], self.ev[e], self.elen[e], self.ekind[e]
                dz = self.elev[v] - self.elev[u]
                pen = self.edge_penalty(e)
                c1 = edge_time_s(L, dz, g, kind) * pen
                c2 = edge_time_s(L, -dz, g, kind) * pen
                cuv.append(c1)
                cvu.append(c2)
                adj[u].append((v, c1, e))
                adj[v].append((u, c2, e))
                radj[v].append((u, c1, e))
                radj[u].append((v, c2, e))
            self.cost_uv[g], self.cost_vu[g] = cuv, cvu
            self.adj[g], self.radj[g] = adj, radj

        # Indeks spasial ruas
        self.nxy = [self.proj.to_xy(self.lat[i], self.lon[i]) for i in range(self.n)]
        self.grid = SegmentGrid(cell_m=80.0)
        for e in range(self.m):
            ax, ay = self.nxy[self.eu[e]]
            bx, by = self.nxy[self.ev[e]]
            self.grid.add(ax, ay, bx, by, e)
        self._natural_cache: Dict[Tuple[str, frozenset], Tuple[List[float], List[int], List[int]]] = {}

    def edge_penalty(self, e: int) -> float:
        """Pengali biaya ruas berisiko saat tsunami (hanya di zona bahaya).

        Rute memilih jalan lain bila ada; bila terpaksa lewat, perkiraan waktunya dibuat lebih pesimistis.
        """
        flag = self.eflag[e] if e < len(self.eflag) else ""
        if not flag:
            return 1.0
        u, v = self.eu[e], self.ev[e]
        if not (self.hazard[u] or self.hazard[v]):
            return 1.0
        if flag == "under":
            return float(self.params.get("underpass_penalty", 10.0))
        if flag == "bridge" and min(self.coast[u], self.coast[v]) < float(self.params.get("bridge_coast_m", 1500.0)):
            return float(self.params.get("bridge_penalty", 3.0))
        return 1.0

    # ---------------- Tsunami: kelayakan gedung ----------------
    def wave(self, wave_m: Optional[float]) -> float:
        return float(wave_m) if wave_m and wave_m > 0 else self.design_wave_m

    def assess(self, b: dict, wave_m: Optional[float] = None) -> dict:
        return tsunami.assess(b, self.wave(wave_m), self.params)

    # ---------------- Snapping ----------------
    def snap(self, lat: float, lon: float) -> Optional[dict]:
        x, y = self.proj.to_xy(lat, lon)
        hit = self.grid.nearest(x, y, max_radius_m=20000)
        if hit is None:
            return None
        d, t, qx, qy, e = hit
        qlat, qlon = self.proj.to_latlon(qx, qy)
        return {"edge": e, "u": self.eu[e], "v": self.ev[e], "t": t, "perp_m": d,
                "lat": qlat, "lon": qlon}

    def start_options(self, s: dict, group: str, blocked_edges: Optional[Set[int]] = None
                      ) -> List[Tuple[int, float]]:
        """Biaya dari posisi (setelah snapping) ke kedua ujung ruas."""
        e, t = s["edge"], s["t"]
        access = flat_time_s(s["perp_m"], group)
        if blocked_edges and e in blocked_edges:
            # Ruas tempat warga berdiri ditandai putus: anggap ia berada di ujung terdekat.
            return [(s["u"] if t < 0.5 else s["v"], access + flat_time_s(min(t, 1 - t) * self.elen[e], group))]
        return [(s["u"], access + t * self.cost_vu[group][e]),
                (s["v"], access + (1 - t) * self.cost_uv[group][e])]

    def _place_target(self, e: int, t: float, perp_m: float, group: str, extra: float
                      ) -> List[Tuple[int, float]]:
        """Biaya dari kedua ujung ruas menuju sebuah tempat di ruas e (posisi t)."""
        acc = flat_time_s(perp_m, group) + extra
        return [(self.eu[e], t * self.cost_uv[group][e] + acc),
                (self.ev[e], (1 - t) * self.cost_vu[group][e] + acc)]

    # ---------------- Tujuan ----------------
    def safe_sources(self) -> Dict[int, Tuple[float, int]]:
        return {i: (0.0, -1) for i in range(self.n) if self.safe[i]}

    def _bdest(self, b: dict, a: dict, typ: str) -> dict:
        return {"type": typ, "id": b["id"], "name": b["name"] or f"Gedung {b['levels']} lantai",
                "kind": b.get("kind"), "lat": b["lat"], "lon": b["lon"], "levels": b["levels"],
                "capacity": a["capacity"], "min_floor": a["min_floor"], "depth_m": a["depth_m"],
                "levels_src": b.get("levels_src", "osm"), "verified": bool(b.get("verified")),
                "structure": b.get("structure", "tidak_tahu"), "access": b.get("access"),
                "edge": b["edge"], "t": b["t"], "perp_m": b["perp_m"], "extra_s": a["climb_s"]}

    def shelter_dests(self, shelter_ids: Iterable[str], wave_m: Optional[float] = None) -> List[dict]:
        """Shelter yang ditetapkan perencana; dibuang bila lantainya tidak cukup tinggi untuk skenario ini."""
        out = []
        for sid in shelter_ids:
            i = self.bindex.get(sid)
            if i is None:
                continue
            b = self.buildings[i]
            a = self.assess(b, wave_m)
            if tsunami.registered_ok(b, a):
                out.append(self._bdest(b, a, "shelter"))
        return out

    def high_ground_nodes(self, wave_m: Optional[float] = None) -> List[int]:
        """Tanah di atas perkiraan tsunami (+ galat DEM) yang belum termasuk zona aman resmi: pilihan darurat."""
        lim = self.wave(wave_m) + float(self.params.get("dem_uncertainty_m", 5.0))
        return [i for i in range(self.n) if not self.safe[i] and self.elev[i] >= lim]

    def gedung_dests(self, wave_m: Optional[float] = None, exclude: Iterable[str] = ()) -> List[dict]:
        """Semua gedung yang memenuhi syarat evakuasi vertikal untuk skenario ini (pilihan terakhir)."""
        ex = set(exclude)
        out = []
        for b in self.buildings:
            if b["id"] in ex:
                continue
            a = self.assess(b, wave_m)
            if a["suitable"]:
                out.append(self._bdest(b, a, "gedung"))
        return out

    def point_dests(self, points: Iterable[dict], alert_active: bool) -> List[dict]:
        out = []
        for p in points:
            if p.get("status") not in OPEN_POINT_STATUSES:
                continue
            s = self.snap(p["lat"], p["lon"])
            if s is None:
                continue
            node = s["u"] if s["t"] < 0.5 else s["v"]
            if alert_active and self.hazard[node]:
                continue  # selama peringatan aktif, titik di zona bahaya tidak ditawarkan
            d = dict(p)
            d.update({"type": "point", "edge": s["edge"], "t": s["t"], "perp_m": s["perp_m"], "extra_s": 0.0})
            out.append(d)
        return out

    def _sources_for(self, dests: List[dict], group: str, include_safe: bool
                     ) -> Dict[int, Tuple[float, int]]:
        src: Dict[int, Tuple[float, int]] = self.safe_sources() if include_safe else {}
        for di, d in enumerate(dests):
            for node, c in self._place_target(d["edge"], d["t"], d["perp_m"], group, d["extra_s"]):
                if node not in src or c < src[node][0]:
                    src[node] = (c, di)
        return src

    def hazard_nodes(self) -> Set[int]:
        return {i for i in range(self.n) if self.hazard[i]}

    # ---------------- Tabel untuk aplikasi warga ----------------
    def live_tables(self, *, closures: Set[int], shelter_ids: Sequence[str], points: Sequence[dict],
                    alert_active: bool, wave_m: Optional[float] = None) -> dict:
        tables = {}
        dests_out = {}
        H = self.wave(wave_m)
        aman_dests = self.shelter_dests(shelter_ids, H)
        # Pilihan terakhir bila tidak sempat ke tanah tinggi: shelter resmi + semua gedung yang layak.
        gedung_dests = aman_dests + self.gedung_dests(H, exclude=[d["id"] for d in aman_dests])
        posko_dests = self.point_dests(points, alert_active)
        blocked_nodes_posko = self.hazard_nodes() if alert_active else set()
        high = self.high_ground_nodes(H)
        hi_idx = len(gedung_dests)
        for g in GROUPS:
            dist, nxt, dest = reverse_multi_source(self.radj[g], self._sources_for(aman_dests, g, True),
                                                   blocked_edges=closures)
            tables[f"aman-{g}"] = _pack(dist, nxt, dest)
            src = self._sources_for(gedung_dests, g, True)
            for i in high:
                if i not in src or src[i][0] > 0:
                    src[i] = (0.0, hi_idx)
            dist, nxt, dest = reverse_multi_source(self.radj[g], src, blocked_edges=closures)
            tables[f"gedung-{g}"] = _pack(dist, nxt, dest)
            dist, nxt, dest = reverse_multi_source(self.radj[g], self._sources_for(posko_dests, g, False),
                                                   blocked_edges=closures, blocked_nodes=blocked_nodes_posko)
            tables[f"posko-{g}"] = _pack(dist, nxt, dest)
        dests_out["aman"] = [_public_dest(d) for d in aman_dests]
        dests_out["gedung"] = [_public_dest(d) for d in gedung_dests] + [
            {"type": "tanah_tinggi", "name": "Tanah lebih tinggi",
             "min_elev": round(H + float(self.params.get("dem_uncertainty_m", 5.0)), 1)}]
        dests_out["posko"] = [_public_dest(d) for d in posko_dests]
        # Biaya berarah tiap ruas agar klien menghitung potongan ruas pertama persis seperti server.
        edge_cost = {g: {"uv": [round(c, 2) for c in self.cost_uv[g]],
                         "vu": [round(c, 2) for c in self.cost_vu[g]]} for g in GROUPS}
        return {"tables": tables, "dests": dests_out, "edge_cost": edge_cost,
                "scenario": {"wave_m": H, "design": wave_m is None or wave_m <= 0}}

    # ---------------- Rute dari satu posisi ----------------
    def route(self, lat: float, lon: float, *, mode: str, group: str, closures: Set[int],
              shelter_ids: Sequence[str], points: Sequence[dict], alert_active: bool,
              alternatives: int = 3, wave_m: Optional[float] = None) -> dict:
        s = self.snap(lat, lon)
        if s is None:
            return {"ok": False, "reason": "Posisi di luar jangkauan data peta."}
        warnings = []
        if s["perp_m"] > 150:
            warnings.append(f"Posisi Anda {s['perp_m']:.0f} m dari jalan terdekat; rute dimulai dari jalan itu.")
        if mode == "aman":
            dests = self.shelter_dests(shelter_ids, wave_m)
            include_safe = True
            blocked_nodes: Set[int] = set()
        elif mode == "gedung":
            dests = self.shelter_dests(shelter_ids, wave_m)
            dests += self.gedung_dests(wave_m, exclude=[d["id"] for d in dests])
            include_safe = False
            blocked_nodes = set()
            high = self.high_ground_nodes(wave_m)
        else:
            dests = self.point_dests(points, alert_active)
            include_safe = False
            blocked_nodes = self.hazard_nodes() if alert_active else set()
        starts = self.start_options(s, group, closures)
        if mode == "aman":
            here = s["u"] if s["t"] < 0.5 else s["v"]
            if self.safe[here] and s["perp_m"] < 60:
                return {"ok": True, "already_safe": True, "snapped": _ll(s), "warnings": warnings,
                        "routes": []}
        # Target untuk pencarian maju
        targets: Dict[int, List[Tuple[int, float]]] = {}
        if include_safe:
            for i in range(self.n):
                if self.safe[i]:
                    targets.setdefault(i, []).append((-1 - i, 0.0))  # tiap node aman = tujuan
        for di, d in enumerate(dests):
            for node, c in self._place_target(d["edge"], d["t"], d["perp_m"], group, d["extra_s"]):
                targets.setdefault(node, []).append((di, c))
        if mode == "gedung":
            for i in high:  # semua tanah tinggi darurat dihitung sebagai satu tujuan
                targets.setdefault(i, []).append((HIGH_GROUND, 0.0))
        # Mode aman: satu rute tercepat (setiap node aman adalah tujuan). Mode posko: beberapa alternatif.
        k = 1 if mode == "aman" else max(1, alternatives)
        found = forward_k_nearest(self.adj[group], starts, targets, k,
                                  blocked_edges=closures, blocked_nodes=blocked_nodes)
        routes = []
        for did, total, path in found:
            coords = [[lon, lat], [s["lon"], s["lat"]]] + [[self.lon[i], self.lat[i]] for i in path]
            if did >= 0:
                d = dests[did]
                coords.append([d["lon"], d["lat"]])
                dest = _public_dest(d)
            elif did == HIGH_GROUND:
                node = path[-1]
                dest = {"type": "tanah_tinggi", "name": "Tanah lebih tinggi", "lat": self.lat[node],
                        "lon": self.lon[node], "elev": self.elev[node],
                        "min_elev": round(self.wave(wave_m) + float(self.params.get("dem_uncertainty_m", 5.0)), 1)}
            else:
                node = -1 - did
                dest = {"type": "safe_zone", "name": "Zona aman", "lat": self.lat[node], "lon": self.lon[node],
                        "elev": self.elev[node]}
            dist_m = _polyline_length(coords)
            routes.append({"dest": dest, "time_s": round(total, 1), "distance_m": round(dist_m, 1),
                           "coords": [[round(c[0], 7), round(c[1], 7)] for c in coords]})
        if not routes:
            reason = {"aman": "Belum ada tempat aman yang terjangkau dari posisi ini.",
                      "gedung": "Tidak ada gedung yang cukup tinggi dan memenuhi syarat di sekitar Anda."}.get(
                mode, "Belum ada posko aktif yang dapat dijangkau dengan aman.")
            return {"ok": False, "reason": reason, "snapped": _ll(s), "warnings": warnings}
        return {"ok": True, "already_safe": False, "snapped": _ll(s), "warnings": warnings, "routes": routes}

    # ---------------- Perencanaan ----------------
    def _natural(self, group: str, closures: frozenset) -> Tuple[List[float], List[int], List[int]]:
        key = (group, closures)
        if key not in self._natural_cache:
            self._natural_cache[key] = reverse_multi_source(self.radj[group], self.safe_sources(),
                                                            blocked_edges=set(closures))
        return self._natural_cache[key]

    def _building_time(self, b: dict, group: str, dist: List[float]) -> float:
        e, t = b["edge"], b["t"]
        acc = flat_time_s(b["perp_m"], group)
        tu = acc + t * self.cost_vu[group][e] + dist[self.eu[e]]
        tv = acc + (1 - t) * self.cost_uv[group][e] + dist[self.ev[e]]
        return min(tu, tv)

    def _people(self, b: dict) -> Dict[str, float]:
        share = float(self.params.get("vulnerable_share", 0.15))
        r = float(b["residents"])
        return {"umum": r * (1 - share), "rentan": r * share}

    def _shelter_reach(self, sid: str, budget_s: float, group: str, closures: Set[int],
                       wave_m: Optional[float] = None) -> List[Tuple[float, int]]:
        """Bangunan hunian yang dapat mencapai shelter sid dalam anggaran waktu."""
        b = self.buildings[self.bindex[sid]]
        a = self.assess(b, wave_m)
        d = [self._bdest(b, a, "shelter")]
        src = self._sources_for(d, group, include_safe=False)
        dist, _, _ = reverse_multi_source(self.radj[group], src, blocked_edges=closures, cutoff=budget_s)
        out = []
        for bi, b in enumerate(self.buildings):
            if b["residents"] <= 0:
                continue
            tt = self._building_time(b, group, dist)
            if tt <= budget_s:
                out.append((tt, bi))
        return out

    def plan(self, *, eta_min: float, delay_min: float, margin_min: float, shelter_ids: Sequence[str],
             closures: Optional[Set[int]] = None, optimize_k: int = 0,
             candidate_ids: Optional[Sequence[str]] = None, design_height_m: Optional[float] = None) -> dict:
        closures = closures or set()
        fz = frozenset(closures)
        H = self.wave(design_height_m)
        sc = {b["id"]: self.assess(b, H) for b in self.buildings}

        def cap_of(sid: str) -> float:
            b, a = self.buildings[self.bindex[sid]], sc[sid]
            if sid in registered and tsunami.registered_ok(b, a):
                return float(a["capacity"])
            return float(a["capacity"]) if a["suitable"] else 0.0

        registered = set(shelter_ids)
        budget = max(0.0, (eta_min - delay_min - margin_min) * 60.0)
        nat_time = {g: [] for g in GROUPS}
        remaining: Dict[Tuple[int, str], float] = {}
        total_people = {g: 0.0 for g in GROUPS}
        red_before = {g: 0.0 for g in GROUPS}
        for g in GROUPS:
            dist, _, _ = self._natural(g, fz)
            for bi, b in enumerate(self.buildings):
                tt = self._building_time(b, g, dist)
                nat_time[g].append(tt)
                ppl = self._people(b)[g]
                total_people[g] += ppl
                if ppl > 0 and tt > budget:
                    remaining[(bi, g)] = ppl
                    red_before[g] += ppl

        reach_cache: Dict[Tuple[str, str], List[Tuple[float, int]]] = {}

        def reach(sid: str, g: str) -> List[Tuple[float, int]]:
            key = (sid, g)
            if key not in reach_cache:
                ok = cap_of(sid) > 0 and sc[sid]["climb_s"] < budget
                reach_cache[key] = self._shelter_reach(sid, budget, g, closures, H) if ok else []
            return reach_cache[key]

        def allocate(sid: str, rem: Dict[Tuple[int, str], float], commit: bool) -> Tuple[float, Dict[str, float]]:
            cap = cap_of(sid)
            entries = []
            for g in GROUPS:
                for tt, bi in reach(sid, g):
                    if rem.get((bi, g), 0) > 0:
                        entries.append((tt, bi, g))
            entries.sort()
            used = 0.0
            by_group = {g: 0.0 for g in GROUPS}
            for tt, bi, g in entries:
                if used >= cap:
                    break
                take = min(cap - used, rem[(bi, g)])
                used += take
                by_group[g] += take
                if commit:
                    rem[(bi, g)] -= take
            return used, by_group

        shelters_out = []
        for sid in shelter_ids:
            if sid not in self.bindex:
                continue
            used, by_g = allocate(sid, remaining, True)
            b = self.buildings[self.bindex[sid]]
            shelters_out.append(_shelter_summary(b, used, by_g, "terdaftar", sc[sid], cap_of(sid)))

        red_after_base = {g: sum(v for (bi, gg), v in remaining.items() if gg == g) for g in GROUPS}

        recommended = []
        curve = [{"k": 0, "red_total": round(sum(red_after_base.values()))}]
        if optimize_k > 0:
            cands = [b["id"] for b in self.buildings if sc[b["id"]]["suitable"] and b["id"] not in registered]
            if candidate_ids:
                allowed = set(candidate_ids)
                cands = [c for c in cands if c in allowed]
            for step in range(optimize_k):
                best = None
                for sid in cands:
                    gain, _ = allocate(sid, remaining, False)
                    if gain <= 0:
                        continue
                    key = (gain, -sc[sid]["climb_s"])
                    if best is None or key > best[0]:
                        best = (key, sid)
                if best is None:
                    break
                sid = best[1]
                used, by_g = allocate(sid, remaining, True)
                cands.remove(sid)
                recommended.append(_shelter_summary(self.buildings[self.bindex[sid]], used, by_g, "rekomendasi",
                                                    sc[sid], cap_of(sid)))
                curve.append({"k": step + 1,
                              "red_total": round(sum(v for v in remaining.values()))})

        red_after = {g: sum(v for (bi, gg), v in remaining.items() if gg == g) for g in GROUPS}
        # Status per bangunan
        bstat = []
        for bi, b in enumerate(self.buildings):
            if b["residents"] <= 0:
                bstat.append(None)
                continue
            ppl = self._people(b)
            red0 = sum(ppl[g] for g in GROUPS if nat_time[g][bi] > budget)
            red1 = sum(remaining.get((bi, g), 0.0) for g in GROUPS)
            if red0 <= 1e-9:
                st = "sempat"
            elif red1 <= 1e-6:
                st = "terlayani_shelter"
            elif red1 < red0 - 1e-6:
                st = "sebagian"
            else:
                st = "tidak_sempat"
            bstat.append(st)
        suit = [b["id"] for b in self.buildings if sc[b["id"]]["suitable"]]
        return {
            "budget_s": budget,
            "wave_m": H,
            "suitable_ids": suit,
            "suitable_count": len(suit),
            "suitable_capacity": sum(sc[i]["capacity"] for i in suit),
            "unknown_levels": sum(1 for b in self.buildings if b.get("kind") != "rumah"
                                  and b.get("levels_src", "osm") == "default"),
            "total_people": {g: round(v) for g, v in total_people.items()},
            "red_before": {g: round(v) for g, v in red_before.items()},
            "red_after_registered": {g: round(v) for g, v in red_after_base.items()},
            "red_after": {g: round(v) for g, v in red_after.items()},
            "shelters": shelters_out,
            "recommended": recommended,
            "curve": curve,
            "building_time": {g: [None if not math.isfinite(t) else round(t) for t in nat_time[g]] for g in GROUPS},
            "building_status": bstat,
        }

    def point_info(self, lat: float, lon: float, *, closures: Set[int], shelter_ids: Sequence[str]) -> dict:
        out = {}
        for g in GROUPS:
            r = self.route(lat, lon, mode="aman", group=g, closures=closures, shelter_ids=shelter_ids,
                           points=[], alert_active=False)
            out[g] = r
        return out


# ---------------------------------------------------------------------------

def _pack(dist: List[float], nxt: List[int], dest: List[int]) -> dict:
    return {
        "time": [round(d) if math.isfinite(d) else -1 for d in dist],
        "next": nxt,
        "dest": [d if d >= -1 else -9 for d in dest],
    }


def _public_dest(d: dict) -> dict:
    keep = ("type", "id", "name", "kind", "lat", "lon", "levels", "capacity", "status", "services",
            "updated_at", "note", "verified", "min_floor", "depth_m", "levels_src", "structure", "access")
    return {k: d[k] for k in keep if k in d}


def _shelter_summary(b: dict, used: float, by_g: Dict[str, float], role: str, a: dict, cap: float) -> dict:
    return {"id": b["id"], "name": b["name"] or f"Gedung {b['levels']} lantai", "kind": b["kind"],
            "levels": b["levels"], "capacity": int(cap), "assigned": round(used),
            "assigned_umum": round(by_g.get("umum", 0)), "assigned_rentan": round(by_g.get("rentan", 0)),
            "lat": b["lat"], "lon": b["lon"], "role": role, "min_floor": a["min_floor"], "depth_m": a["depth_m"],
            "usable": cap > 0, "reason": a["reason"] if cap <= 0 else "",
            "levels_src": b.get("levels_src", "osm"), "verified": bool(b.get("verified"))}


def _ll(s: dict) -> dict:
    return {"lat": s["lat"], "lon": s["lon"], "perp_m": round(s["perp_m"], 1)}


def _polyline_length(coords: Sequence[Sequence[float]]) -> float:
    return sum(haversine_m(a[1], a[0], b[1], b[0]) for a, b in zip(coords[:-1], coords[1:]))
