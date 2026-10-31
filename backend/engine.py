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
- Kapasitas: alokasi berkapasitas KERAS (backend/allocation.py, LP leksikografis HiGHS + rekonsiliasi bulat +
  validasi independen). Dual kapasitas dipakai sebagai offset per gedung di tabel HP; asal yang tidak bisa diwakili
  tabel dikirim sebagai `overrides`. Tabel offline, rute online, dasbor, dan perencana memakai hasil alokasi yang sama.
  Lihat docs/capacity/DESIGN.md. (Penalti "lelang" lama dihapus; pembanding: tools/bench_capacity.py --old-repo.)
- Akses gedung mengikuti waktu peringatan (jam buka hasil survei, zona waktu area) di rute, tabel, dan optimizer.
"""
from __future__ import annotations

import heapq
import math
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import datetime as _dt
import threading
import time as _time

from . import allocation as AL
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


def reverse_k_best(radj: Adjacency, sources: Sequence[Tuple[int, float, int]], k: int,
                   blocked_edges: Optional[Set[int]] = None, bound: Optional[Sequence[float]] = None
                   ) -> List[List[Tuple[float, int]]]:
    """Hingga k tujuan BERBEDA termurah untuk setiap node (Dijkstra dengan k label per node).

    sources: [(node, biaya awal, id tujuan)]; satu node boleh menjadi awal beberapa tujuan.
    bound[x]: label dengan biaya >= bound[x] tidak disimpan (mis. tujuan tanpa batas kapasitas sudah lebih murah).
    Mengembalikan labels[x] = [(biaya, id tujuan), ...] terurut naik.
    """
    n = len(radj)
    labels: List[List[Tuple[float, int]]] = [[] for _ in range(n)]
    seen: List[set] = [set() for _ in range(n)]
    be = blocked_edges or set()
    heap = [(c0, node, did) for node, c0, did in sources if bound is None or c0 < bound[node]]
    heapq.heapify(heap)
    while heap:
        d, y, sid = heapq.heappop(heap)
        if len(labels[y]) >= k or sid in seen[y]:
            continue
        labels[y].append((d, sid))
        seen[y].add(sid)
        for x, c, e in radj[y]:
            if e in be or len(labels[x]) >= k or sid in seen[x]:
                continue
            nd = d + c
            if bound is not None and nd >= bound[x]:
                continue
            heapq.heappush(heap, (nd, x, sid))
    return labels


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
        self._alloc: Dict[tuple, dict] = {}          # hasil alokasi per kunci skenario
        self._alloc_lock = threading.Lock()
        self._demand_cache: Optional[List[Tuple[int, Dict[str, int]]]] = None

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

    def assess(self, b: dict, wave_m: Optional[float] = None, at: Optional[Sequence] = None) -> dict:
        """at = (unix_ts peringatan, bujur) untuk gedung berakses jam kerja; None = tanpa batasan waktu."""
        return tsunami.assess(b, self.wave(wave_m), self.params, at)

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
                "levels_src": b.get("levels_src", "osm"), "surveyed": tsunami._surveyed(b),
                "min_floor_nominal": a.get("min_floor_nominal"), "depth_nominal_m": a.get("depth_nominal_m"),
                "elev_margin_m": a.get("elev_margin_m"), "destination": a.get("destination", False),
                "structure": b.get("structure", "tidak_tahu"), "access": b.get("access"),
                "open_hours": b.get("open_hours"), "open_days": b.get("open_days"),
                "access_state": a.get("access_state"), "elev_conf": a.get("elev_conf"),
                "tier": a["tier"], "tier_label": a.get("tier_label"), "caveats": a["caveats"],
                "edge": b["edge"], "t": b["t"], "perp_m": b["perp_m"], "extra_s": a["climb_s"]}

    def shelter_dests(self, shelter_ids: Iterable[str], wave_m: Optional[float] = None,
                      at: Optional[Sequence] = None) -> List[dict]:
        """Shelter RESMI: terdaftar DAN terverifikasi (tsunami.registered_status); dibuang bila tidak layak, belum
        terverifikasi, terlalu rendah, kapasitas 0, atau tutup pada waktu peringatan. Shelter terdaftar yang belum
        terverifikasi tetap bisa muncul sebagai pilihan darurat "Perlu Verifikasi" lewat gedung_dests."""
        out = []
        for sid in shelter_ids:
            i = self.bindex.get(sid)
            if i is None:
                continue
            b = self.buildings[i]
            a = self.assess(b, wave_m, at)
            if tsunami.registered_ok(b, a):
                out.append(self._bdest(b, a, "shelter"))
        return out

    def high_ground_nodes(self, wave_m: Optional[float] = None) -> List[int]:
        """Tanah di atas perkiraan tsunami (+ galat DEM) yang belum termasuk zona aman resmi: pilihan darurat."""
        lim = self.wave(wave_m) + float(self.params.get("dem_uncertainty_m", 5.0))
        return [i for i in range(self.n) if not self.safe[i] and self.elev[i] >= lim]

    def gedung_dests(self, wave_m: Optional[float] = None, exclude: Iterable[str] = (),
                     at: Optional[Sequence] = None) -> List[dict]:
        """Gedung evakuasi vertikal (belum terdaftar) yang boleh menjadi TUJUAN rute & alokasi pada skenario ini:
        `assess(...)["destination"]` = lantai aman konservatif terpenuhi, terbuka, kapasitas > 0, DAN terverifikasi.

        Gedung "Perlu Verifikasi" TIDAK pernah menjadi tujuan (kebijakan 1 Nov 2026); lihat `unverified_info`.
        """
        ex = set(exclude)
        out = []
        for b in self.buildings:
            if b["id"] in ex:
                continue
            a = self.assess(b, wave_m, at)
            if a["destination"]:
                out.append(self._bdest(b, a, "gedung"))
        return out

    def unverified_info(self, wave_m: Optional[float] = None, at: Optional[Sequence] = None) -> List[dict]:
        """Gedung yang lolos lantai aman konservatif tetapi BELUM terverifikasi: daftar verifikasi BPBD, dan bagi
        warga hanya informasi TANPA rute dan TANPA alokasi (label "belum diverifikasi, bukan rekomendasi")."""
        out = []
        for b in self.buildings:
            a = self.assess(b, wave_m, at)
            if a["suitable"] and a["tier"] == "unverified":
                d = self._bdest(b, a, "unverified")
                out.append({k: d[k] for k in ("type", "id", "name", "kind", "lat", "lon", "levels", "min_floor",
                                               "min_floor_nominal", "elev_margin_m", "levels_src", "tier",
                                               "tier_label", "caveats", "surveyed")})
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

    def _sources_for(self, dests: List[dict], group: str, include_safe: bool,
                     pen: Optional[Sequence[float]] = None, high: Sequence[int] = (), hi_idx: int = -1
                     ) -> Dict[int, Tuple[float, int]]:
        """Sumber Dijkstra terbalik. pen = penalti kapasitas per tujuan; high = node tanah tinggi (tujuan hi_idx)."""
        src: Dict[int, Tuple[float, int]] = self.safe_sources() if include_safe else {}
        for di, d in enumerate(dests):
            extra = d["extra_s"] + (pen[di] if pen else 0.0)
            for node, c in self._place_target(d["edge"], d["t"], d["perp_m"], group, extra):
                if node not in src or c < src[node][0]:
                    src[node] = (c, di)
        for i in high:
            if i not in src or src[i][0] > 0:
                src[i] = (0.0, hi_idx)
        return src

    def _end_costs(self, b: dict, group: str) -> Tuple[float, float]:
        e, t = b["edge"], b["t"]
        acc = flat_time_s(b["perp_m"], group)
        return acc + t * self.cost_vu[group][e], acc + (1 - t) * self.cost_uv[group][e]

    # ---------------- Kapasitas pada rute warga ----------------
    def demand(self) -> List[Tuple[int, Dict[str, int]]]:
        """Penduduk model per bangunan sebagai orang BULAT (largest remainder; total area dipertahankan).

        [(indeks bangunan, {"umum": n, "rentan": m})] untuk bangunan berpenghuni. Ini perkiraan model (asumsi orang per
        rumah dsb.), bukan data kependudukan.
        """
        if self._demand_cache is None:
            res = [max(0.0, float(b.get("residents") or 0)) for b in self.buildings]
            tot = AL.largest_remainder(res)
            share = float(self.params.get("vulnerable_share", 0.15))
            ren = AL.largest_remainder([t * share for t in tot])
            self._demand_cache = [(bi, {"umum": t - min(r, t), "rentan": min(r, t)})
                                  for bi, (t, r) in enumerate(zip(tot, ren)) if t > 0]
        return self._demand_cache

    def demand_info(self) -> dict:
        model = sum(max(0.0, float(b.get("residents") or 0)) for b in self.buildings)
        pop = self.area.get("meta", {}).get("population") or {}
        return {"model_total": round(model, 2), "people": sum(sum(q.values()) for _, q in self.demand()),
                # sumber penduduk: "grid" (diunggah, mis. HRSL) atau "model" (orang per rumah) — selalu berlabel
                "method": pop.get("method", "model"), "label": pop.get("label"), "synthetic": bool(pop.get("synthetic")),
                "virtual_origins": pop.get("virtual_origins", 0)}

    def model_info(self) -> dict:
        """Versi model & parameter yang menghasilkan keputusan (provenance di tabel HP, dasbor, dan PDF)."""
        import hashlib as _h
        import json as _j
        from . import __version__
        blob = _j.dumps(self.params, sort_keys=True, separators=(",", ":"), default=str).encode()
        meta = self.area.get("meta", {})
        return {"sempat": __version__, "schema": meta.get("schema"), "params_sha": _h.sha1(blob).hexdigest()[:12],
                "snapshot": meta.get("snapshot"), "population": (meta.get("population") or {}).get("method", "model")}

    def budget_s(self, eta_min: Optional[float]) -> float:
        """Anggaran waktu jalan (s) = (ETA - jeda mulai - margin) menit; ETA kosong -> params default_eta_min."""
        eta = float(eta_min) if eta_min else float(self.params.get("default_eta_min", 20.0))
        return max(0.0, (eta - float(self.params.get("alloc_delay_min", 5.0))
                         - float(self.params.get("alloc_margin_min", 2.0))) * 60.0)

    def deadline_policy(self) -> dict:
        """SATU definisi batas waktu untuk alokasi, perencana, dan HP (core.js evacTiming):
        batas tiba = waktu peringatan + ETA - margin. Alokasi/perencana mengasumsikan warga mulai bergerak
        `start_delay_s` setelah peringatan (anggaran jalan = ETA - jeda - margin); HP memakai waktu sekarang yang nyata.
        """
        return {"margin_s": float(self.params.get("alloc_margin_min", 2.0)) * 60.0,
                "start_delay_s": float(self.params.get("alloc_delay_min", 5.0)) * 60.0,
                "default_eta_min": float(self.params.get("default_eta_min", 20.0))}

    def alloc_key(self, mode: str, dests: List[dict], closures: Set[int], H: float, budget_s: Optional[float]) -> tuple:
        return (mode, frozenset(closures), round(H, 3), None if budget_s is None else round(budget_s, 1),
                tuple((d["id"], int(d.get("capacity") or 0), round(d["extra_s"], 2)) for d in dests))

    def alloc_cached(self, key: tuple) -> Optional[dict]:
        with self._alloc_lock:
            return self._alloc.get(key)

    def _unlimited(self, g: str, high: Sequence[int], closures: Set[int]):
        src = self.safe_sources()
        for i in high:
            src[i] = (0.0, -1)
        return reverse_multi_source(self.radj[g], src, blocked_edges=closures)

    def allocate(self, mode: str, dests: List[dict], *, closures: Set[int], high: Sequence[int], H: float,
                 budget_s: Optional[float], k: int = 8, time_limit_s: float = 60.0, use_lp: bool = True,
                 cache: bool = True, prioritize_deadline: Optional[bool] = None) -> dict:
        """Alokasi berkapasitas KERAS untuk himpunan tujuan `dests` + zona aman (+ tanah tinggi).

        Mengembalikan outcome: hasil allocation.solve + offset tabel (pen), overrides HP, dan pemeriksaan "seperti HP".
        """
        key = self.alloc_key(mode, dests, closures, H, budget_s)
        if cache:
            got = self.alloc_cached(key)
            if got is not None:
                return got
        t0 = _time.perf_counter()
        nd = len(dests)
        unl = {g: self._unlimited(g, high, closures) for g in GROUPS}
        origins: List[AL.Origin] = []
        oinfo: List[Tuple[int, str]] = []
        t_c = _time.perf_counter()
        for g in GROUPS:
            srcs = [(node, c, di) for di, d in enumerate(dests)
                    for node, c in self._place_target(d["edge"], d["t"], d["perp_m"], g, d["extra_s"])]
            labels = reverse_k_best(self.radj[g], srcs, k, closures, bound=unl[g][0]) if nd else None
            for bi, q in self.demand():
                if q[g] <= 0:
                    continue
                b = self.buildings[bi]
                cu, cv = self._end_costs(b, g)
                u, v = self.eu[b["edge"]], self.ev[b["edge"]]
                cands: Dict[int, float] = {}
                if labels is not None:
                    for acc, node in ((cu, u), (cv, v)):
                        for c, sid in labels[node]:
                            if acc + c < cands.get(sid, INF):
                                cands[sid] = acc + c
                fb = min(cu + unl[g][0][u], cv + unl[g][0][v])
                origins.append(AL.Origin(f"{b['id']}|{g}", q[g], cands, fb, {"bi": bi, "g": g}))
                oinfo.append((bi, g))
        t_cand = _time.perf_counter() - t_c
        problem = AL.Problem(origins, dests, budget_s)
        by_group = {g: [i for i, (_, gg) in enumerate(oinfo) if gg == g] for g in GROUPS}

        def price_check(duals: List[float], meta: dict) -> int:
            """Kolom baru: tujuan di luar kandidat yang reduced cost-nya negatif (Dijkstra 3-label dengan offset dual)."""
            y = dict(zip(meta["rows"], meta["eq_duals"]))
            lam = meta["lock_duals"][-1] if meta.get("deadline_s") is not None and meta["lock_duals"] else 0.0
            T = meta.get("deadline_s")
            added = 0
            for g in GROUPS:
                srcs = [(node, c, di) for di, d in enumerate(dests)
                        for node, c in self._place_target(d["edge"], d["t"], d["perp_m"], g, d["extra_s"] + duals[di])]
                if not srcs:
                    continue
                lab = reverse_k_best(self.radj[g], srcs, 3, closures, bound=unl[g][0])
                for oi in by_group[g]:
                    if oi not in y:
                        continue
                    o = origins[oi]
                    b = self.buildings[o.info["bi"]]
                    cu, cv = self._end_costs(b, g)
                    u, v = self.eu[b["edge"]], self.ev[b["edge"]]
                    for acc, node in ((cu, u), (cv, v)):
                        for val, sid in lab[node]:
                            if sid in o.cands:
                                continue
                            c = acc + val - duals[sid]
                            late = T is not None and c > T + 1e-9
                            if c + duals[sid] + (lam if late else 0.0) - y[oi] < -1e-6:
                                o.cands[sid] = c
                                added += 1
            return added

        res = AL.solve(problem, time_limit_s=time_limit_s, price_check=price_check if nd else None, use_lp=use_lp,
                       prioritize_deadline=(bool(self.params.get("alloc_prioritize_deadline", True))
                                            if prioritize_deadline is None else prioritize_deadline))
        res["meta"]["timing"]["candidates_s"] = round(t_cand, 3)
        out = self._encode_allocation(mode, dests, closures, high, problem, oinfo, res, unl)
        out["meta_demand"] = self.demand_info()
        out["key"] = key
        out["mode"] = mode
        out["origins"] = [(o.info["bi"], o.info["g"], dict(o.cands), o.fallback) for o in origins]
        out["computed_at"] = _time.time()
        out["result"]["meta"]["timing"]["all_s"] = round(_time.perf_counter() - t0, 3)
        if cache:
            with self._alloc_lock:
                if len(self._alloc) > 16:
                    self._alloc.clear()
                self._alloc[key] = out
        return out

    def _encode_allocation(self, mode, dests, closures, high, problem, oinfo, res, unl) -> dict:
        """Dual kapasitas -> offset tabel; asal yang tidak terwakili tabel -> overrides; lalu cek beban SEPERTI HP."""
        t0 = _time.perf_counter()
        nd = len(dests)
        pen = [round(p, 2) for p in res["duals"]] if res["status"] != "gagal" else [0.0] * nd
        overrides: Dict[str, List[dict]] = {g: [] for g in GROUPS}
        table_load = [0] * nd
        table_unl = 0
        trees: Dict[Tuple[str, int], Tuple[List[float], List[int]]] = {}
        need: Dict[Tuple[str, int], float] = {}
        info: Dict[str, List[Tuple[int, int, Optional[int], Dict[int, int]]]] = {g: [] for g in GROUPS}
        errs: List[str] = []
        flows = res["flows"]
        for g in GROUPS:
            dist, nxt, dst = reverse_multi_source(self.radj[g], self._sources_for(dests, g, True, pen, high, nd),
                                                  blocked_edges=closures)
            tdist = [round(x) if math.isfinite(x) else -1 for x in dist]  # sama dengan _pack (yang dibaca HP)
            ec_uv = [round(c, 2) for c in self.cost_uv[g]]
            ec_vu = [round(c, 2) for c in self.cost_vu[g]]
            sp = GROUP_SPEED_MPS[g]
            for oi, (bi, gg) in enumerate(oinfo):
                if gg != g:
                    continue
                o = problem.origins[oi]
                b = self.buildings[bi]
                e = b["edge"]
                u, v = self.eu[e], self.ev[e]
                # pilihan ujung PERSIS seperti core.js routeFromTable (biaya ruas 2 desimal + waktu tabel bulat)
                opts = [(u, b["perp_m"] / sp + b["t"] * ec_vu[e]), (v, b["perp_m"] / sp + (1 - b["t"]) * ec_uv[e])]
                best = None
                for node, c in opts:
                    if tdist[node] < 0:
                        continue
                    if best is None or c + tdist[node] < best[1]:
                        best = (node, c + tdist[node])
                tab = None
                if best is not None:
                    x = best[0]
                    while nxt[x] != -1:
                        x = nxt[x]
                    di = dst[x]
                    tab = di if 0 <= di < nd else AL.UNLIMITED
                f = {d: n for d, n in flows.get(oi, {}).items() if n > 0}
                if f:
                    info[g].append((oi, bi, tab, f))
        # Asal yang tidak terwakili tabel -> override. HP mencocokkan override pada ruas yang sama dalam radius 25 m
        # (yang terdekat), jadi bangunan TETANGGA dalam radius itu juga diberi override (tujuan alokasinya sendiri)
        # agar HP di bangunan mana pun mengikuti alokasi bangunan itu. Bangunan di titik yang persis sama digabung.
        for g in GROUPS:
            lst = info[g]
            by_edge: Dict[int, List[int]] = {}
            for k, (oi, bi, tab, f) in enumerate(lst):
                by_edge.setdefault(self.buildings[bi]["edge"], []).append(k)
            mark = [not (len(f) == 1 and tab is not None and next(iter(f)) == tab) for (_, _, tab, f) in lst]
            stack = [k for k in range(len(lst)) if mark[k]]
            while stack:
                k = stack.pop()
                bk = self.buildings[lst[k][1]]
                L = self.elen[bk["edge"]]
                for j in by_edge[bk["edge"]]:
                    if not mark[j] and abs(self.buildings[lst[j][1]]["t"] - bk["t"]) * L <= self.OVERRIDE_RADIUS_M:
                        mark[j] = True
                        stack.append(j)
            recs: Dict[Tuple[int, float], dict] = {}
            for k, (oi, bi, tab, f) in enumerate(lst):
                if not mark[k]:
                    continue
                b = self.buildings[bi]
                key = (b["edge"], round(b["t"], 6))
                rec = recs.get(key)
                if rec is None:
                    rec = recs[key] = {"o": b["id"], "e": b["edge"], "t": b["t"], "tab": tab, "unres": 0, "opts": [],
                                       "_oi": oi, "_n": {}, "_members": []}
                rec["_members"].append(oi)
                rec["unres"] += f.get(AL.UNRESOLVED, 0)
                for d, n in f.items():
                    if d == AL.UNRESOLVED:
                        continue
                    rec["_n"][d] = rec["_n"].get(d, 0) + n
                    if d >= 0:
                        need[(g, d)] = max(need.get((g, d), 0.0), problem.origins[oi].cands.get(d, 0.0))
            for rec in recs.values():
                rec["opts"] = [{"d": d, "n": n} for d, n in sorted(rec.pop("_n").items())]
                overrides[g].append(rec)
        # Jalur untuk overrides: satu pohon Dijkstra per (kelompok, gedung) yang dipakai, dipotong di biaya terjauh.
        for (g, d), cmax in need.items():
            dd = dests[d]
            src = {node: (c, d) for node, c in self._place_target(dd["edge"], dd["t"], dd["perp_m"], g, dd["extra_s"])}
            dist2, nxt2, _ = reverse_multi_source(self.radj[g], src, blocked_edges=closures, cutoff=cmax + 1.0)
            trees[(g, d)] = (dist2, nxt2)
        for g in GROUPS:
            for rec in overrides[g]:
                o = problem.origins[rec.pop("_oi")]
                b = self.buildings[o.info["bi"]]
                for opt in rec["opts"]:
                    path, end_cost = self._path_to(g, opt["d"], b, dests, closures, unl, trees)
                    if path is None:
                        errs.append(f"{o.key}: jalur ke tujuan alokasi tidak ditemukan")
                        continue
                    opt["s"] = round(end_cost, 1)
                    opt["nodes"] = path
        # Simulasi HP di setiap bangunan berpenghuni: override yang cocok (aturan _override_at) atau tabel.
        for g in GROUPS:
            rec_edge: Dict[int, List[dict]] = {}
            for rec in overrides[g]:
                rec_edge.setdefault(rec["e"], []).append(rec)
            own = {oi: rec for rec in overrides[g] for oi in rec["_members"]}
            for oi, bi, tab, f in info[g]:
                b = self.buildings[bi]
                hit = self._override_at({"edge": b["edge"], "t": b["t"]}, rec_edge.get(b["edge"], []))
                if hit is not None:
                    if own.get(oi) is not hit:
                        errs.append(f"{problem.origins[oi].key}: HP di bangunan ini memakai override bangunan lain")
                    continue
                if oi in own or not (len(f) == 1 and next(iter(f)) == tab):
                    errs.append(f"{problem.origins[oi].key}: alokasi tidak terwakili tabel maupun override")
                    continue
                if tab >= 0:
                    table_load[tab] += f[tab]
                else:
                    table_unl += f[tab]
            for rec in overrides[g]:
                rec.pop("_members")
                for opt in rec["opts"]:
                    if "nodes" not in opt:
                        continue
                    if opt["d"] >= 0:
                        table_load[opt["d"]] += opt["n"]
                    else:
                        table_unl += opt["n"]
        # Beban "seperti HP" (tabel + overrides) harus SAMA dengan alokasi; selain itu hasil tidak dipakai.
        if res["status"] != "gagal":
            for d in range(nd):
                if table_load[d] != res["load"][d]:
                    errs.append(f"beban HP gedung {dests[d]['id']} = {table_load[d]} != alokasi {res['load'][d]}")
                if table_load[d] > int(dests[d].get("capacity") or 0):
                    errs.append(f"beban HP gedung {dests[d]['id']} melebihi kapasitas")
        status = res["status"]
        if errs and status != "gagal":
            status = "gagal"
            pen = [0.0] * nd
            overrides = {g: [] for g in GROUPS}
        res["meta"]["timing"]["encode_s"] = round(_time.perf_counter() - t0, 3)
        return {"status": status, "result": res, "pen": pen, "overrides": overrides, "table_errors": errs[:20],
                "table_load": table_load, "table_unlimited": table_unl,
                "n_overrides": sum(len(v) for v in overrides.values())}

    def _path_to(self, g, d, b, dests, closures, unl, trees):
        """Jalur simpul dari ujung ruas bangunan ke tujuan d (indeks gedung) atau zona aman (AL.UNLIMITED)."""
        if d == AL.UNLIMITED:
            dist, nxt = unl[g][0], unl[g][1]
        else:
            key = (g, d)
            if key not in trees:
                dd = dests[d]
                src = {node: (c, d) for node, c in self._place_target(dd["edge"], dd["t"], dd["perp_m"], g, dd["extra_s"])}
                dist2, nxt2, _ = reverse_multi_source(self.radj[g], src, blocked_edges=closures)
                trees[key] = (dist2, nxt2)
            dist, nxt = trees[key]
        cu, cv = self._end_costs(b, g)
        u, v = self.eu[b["edge"]], self.ev[b["edge"]]
        end, cost = min(((u, cu + dist[u]), (v, cv + dist[v])), key=lambda t: t[1])
        if not math.isfinite(cost):
            return None, INF
        path = [end]
        x, guard = end, 0
        while nxt[x] != -1 and guard < 200000:
            x = nxt[x]
            path.append(x)
            guard += 1
        return path, cost

    def capacity_view(self, out: Optional[dict], dests: List[dict], status: str, H: float,
                      budget_s: Optional[float], messages: Sequence[str] = ()) -> dict:
        """Ringkasan kapasitas untuk tabel/dasbor (publik: tanpa data per orang)."""
        view = {"status": status, "wave_m": H, "deadline_s": budget_s, "messages": list(messages),
                "population": self.demand_info()}
        if out is None or status in ("pending", "tanpa_penduduk", "tanpa_gedung", "gagal"):
            view["dests"] = [dict(_occ_fields(d), id=d["id"], name=d.get("name"), type=d.get("type"),
                                  tier=d.get("tier"), capacity=int(d.get("capacity") or 0)) for d in dests]
            return view
        r = out["result"]
        view.update({
            "method": r["meta"]["method"], "optimal": r["meta"]["optimal"], "computed_at": _dt.datetime.fromtimestamp(out["computed_at"], _dt.timezone.utc).isoformat(timespec="milliseconds"),
            "timing": r["meta"]["timing"], "totals": {k: v for k, v in r["totals"].items() if k != "time_sum"},
            "overrides": out["n_overrides"], "errors": (r["errors"] + out["table_errors"])[:10],
            "messages": list(messages) + r["meta"]["messages"],
            "unlimited": {"allocated": r["totals"]["to_unlimited"], "capacity_known": False,
                          "label": "Zona aman / tanah tinggi: kapasitas tidak diketahui (dimodelkan tanpa batas)"},
            "dests": [dict(_occ_fields(d), id=d["id"], name=d.get("name"), type=d.get("type"), tier=d.get("tier"),
                           capacity=int(d.get("capacity") or 0), allocated=r["load"][i],
                           remaining=r["remaining"][i], full=r["remaining"][i] <= 0)
                      for i, d in enumerate(dests)]})
        return view

    def hazard_nodes(self) -> Set[int]:
        return {i for i in range(self.n) if self.hazard[i]}

    # ---------------- Tabel untuk aplikasi warga ----------------
    def mode_dests(self, mode: str, *, shelter_ids: Sequence[str], H: float, at=None,
                   occupancy: Optional[Dict[str, int]] = None) -> Tuple[List[dict], List[int]]:
        """Tujuan berkapasitas + node tanah tinggi untuk mode "aman" / "gedung" (sama untuk tabel, rute, dasbor)."""
        aman = self.shelter_dests(shelter_ids, H, at)
        if mode == "aman":
            dests, high = aman, []
        else:
            dests = aman + self.gedung_dests(H, exclude=[d["id"] for d in aman], at=at)
            high = self.high_ground_nodes(H)
        if occupancy:
            dests = [dict(d, capacity_design=d["capacity"], occupied=int(occupancy.get(d["id"], 0)),
                          capacity=max(0, int(d["capacity"]) - int(occupancy.get(d["id"], 0))))
                     if d["id"] in occupancy else d for d in dests]
        return dests, high

    def mode_allocation(self, mode: str, dests: List[dict], high: Sequence[int], *, closures: Set[int], H: float,
                        budget_s: Optional[float], alloc: str = "sync") -> Tuple[List[float], dict, Optional[dict], dict]:
        """(offset tabel, overrides, outcome atau None, ringkasan kapasitas). alloc: "sync" | "cached" | "off"."""
        zero = [0.0] * len(dests)
        empty = {g: [] for g in GROUPS}
        if not self.demand():
            return zero, empty, None, self.capacity_view(None, dests, "tanpa_penduduk", H, budget_s, [
                "Data penduduk tidak tersedia di peta ini: kapasitas TIDAK dihitung (rute = tujuan terdekat)."])
        if not dests:
            return zero, empty, None, self.capacity_view(None, dests, "tanpa_gedung", H, budget_s, [
                "Tidak ada gedung berkapasitas untuk mode ini; tujuan = zona aman/tanah tinggi."])
        key = self.alloc_key(mode, dests, closures, H, budget_s)
        out = self.alloc_cached(key)
        if out is None and alloc == "sync":
            out = self.allocate(mode, dests, closures=closures, high=high, H=H, budget_s=budget_s)
        if out is None:
            return zero, empty, None, self.capacity_view(None, dests, "pending", H, budget_s, [
                "Kapasitas sedang dihitung: rute sementara = tujuan terdekat TANPA batas kapasitas."])
        if out["status"] == "gagal":
            errs = list((out.get("result") or {}).get("errors") or []) + list(out.get("table_errors") or [])
            return zero, empty, out, self.capacity_view(None, dests, "gagal", H, budget_s, [
                "Alokasi kapasitas gagal / tidak lolos validasi: rute = tujuan terdekat TANPA batas kapasitas."]
                + errs[:5])
        return out["pen"], out["overrides"], out, self.capacity_view(out, dests, out["status"], H, budget_s)

    def live_tables(self, *, closures: Set[int], shelter_ids: Sequence[str], points: Sequence[dict],
                    alert_active: bool, wave_m: Optional[float] = None, at: Optional[Sequence] = None,
                    eta_min: Optional[float] = None, alloc: str = "sync",
                    occupancy: Optional[Dict[str, int]] = None) -> dict:
        """Tabel HP. alloc="sync": hitung alokasi bila belum ada (pemakaian langsung/uji); "cached": hanya pakai hasil
        yang sudah ada untuk skenario PERSIS ini, selain itu status "pending" + permintaan di `_alloc_requests`."""
        tables = {}
        dests_out = {}
        H = self.wave(wave_m)
        budget = self.budget_s(eta_min)
        aman_dests, _ = self.mode_dests("aman", shelter_ids=shelter_ids, H=H, at=at, occupancy=occupancy)
        gedung_dests, high = self.mode_dests("gedung", shelter_ids=shelter_ids, H=H, at=at, occupancy=occupancy)
        posko_dests = self.point_dests(points, alert_active)
        blocked_nodes_posko = self.hazard_nodes() if alert_active else set()
        hi_idx = len(gedung_dests)
        pen_a, ov_a, out_a, view_a = self.mode_allocation("aman", aman_dests, (), closures=closures, H=H,
                                                           budget_s=budget, alloc=alloc)
        pen_g, ov_g, out_g, view_g = self.mode_allocation("gedung", gedung_dests, high, closures=closures, H=H,
                                                           budget_s=budget, alloc=alloc)
        requests = []
        for mode, view, dl, hg in (("aman", view_a, aman_dests, ()), ("gedung", view_g, gedung_dests, high)):
            if view["status"] == "pending":
                requests.append({"mode": mode, "dests": dl, "high": list(hg), "closures": set(closures), "H": H,
                                 "budget_s": budget})
        cap_a = _compat_check(out_a, aman_dests)
        cap_g = _compat_check(out_g, gedung_dests)
        for g in GROUPS:
            dist, nxt, dest = reverse_multi_source(self.radj[g], self._sources_for(aman_dests, g, True, pen_a),
                                                   blocked_edges=closures)
            tables[f"aman-{g}"] = _pack(dist, nxt, dest)
            src = self._sources_for(gedung_dests, g, True, pen_g, high, hi_idx)
            dist, nxt, dest = reverse_multi_source(self.radj[g], src, blocked_edges=closures)
            tables[f"gedung-{g}"] = _pack(dist, nxt, dest)
            dist, nxt, dest = reverse_multi_source(self.radj[g], self._sources_for(posko_dests, g, False),
                                                   blocked_edges=closures, blocked_nodes=blocked_nodes_posko)
            tables[f"posko-{g}"] = _pack(dist, nxt, dest)
        dests_out["aman"] = [_public_dest(d, p) for d, p in zip(aman_dests, pen_a)]
        dests_out["gedung"] = [_public_dest(d, p) for d, p in zip(gedung_dests, pen_g)] + [
            {"type": "tanah_tinggi", "name": "Tanah lebih tinggi",
             "min_elev": round(H + float(self.params.get("dem_uncertainty_m", 5.0)), 1)}]
        dests_out["posko"] = [_public_dest(d) for d in posko_dests]
        # Gedung Perlu Verifikasi: hanya informasi (tanpa tabel rute, tanpa alokasi), lihat unverified_info.
        dests_out["unverified"] = self.unverified_info(H, at)
        # Biaya berarah tiap ruas agar klien menghitung potongan ruas pertama persis seperti server.
        edge_cost = {g: {"uv": [round(c, 2) for c in self.cost_uv[g]],
                         "vu": [round(c, 2) for c in self.cost_vu[g]]} for g in GROUPS}
        at_c = tsunami._coerce_at(at)
        overrides = {f"aman-{g}": ov_a.get(g, []) for g in GROUPS}
        overrides.update({f"gedung-{g}": ov_g.get(g, []) for g in GROUPS})
        return {"tables": tables, "dests": dests_out, "edge_cost": edge_cost,
                "scenario": {"wave_m": H, "design": wave_m is None or wave_m <= 0,
                             "time": _time_info(at_c)},
                "capacity": {"aman": view_a, "gedung": view_g},
                "deadline": self.deadline_policy(),
                "model": self.model_info(),
                "overrides": overrides,
                "_alloc_requests": requests,
                "capacity_check": {"aman": cap_a, "gedung": cap_g},
                # Tanpa peringatan (jam sekarang): kapan status buka/tutup gedung berikutnya berubah, agar server
                # menghitung ulang dan HP offline menandai tabel kedaluwarsa. Saat peringatan, akses dinilai pada jam
                # peringatan s.d. perkiraan gelombang tiba, jadi tabel berlaku selama peringatan itu.
                "valid_until": None if alert_active else tsunami.next_access_change(self.buildings, at_c)}

    # ---------------- Rute dari satu posisi ----------------
    def route(self, lat: float, lon: float, *, mode: str, group: str, closures: Set[int],
              shelter_ids: Sequence[str], points: Sequence[dict], alert_active: bool,
              alternatives: int = 3, wave_m: Optional[float] = None, at: Optional[Sequence] = None,
              eta_min: Optional[float] = None, alloc: str = "sync", device_key: Optional[str] = None,
              occupancy: Optional[Dict[str, int]] = None) -> dict:
        """Rute online. Mode aman/gedung memakai alokasi yang SAMA dengan tabel HP (offset dual + overrides).

        alloc="cached" (server): hanya hasil alokasi yang sudah ada; bila belum ada -> tanpa batas kapasitas dan
        `capacity.status = "pending"`. device_key: memilih opsi asal yang dipecah persis seperti HP.
        """
        s = self.snap(lat, lon)
        if s is None:
            return {"ok": False, "reason": "Posisi di luar jangkauan data peta."}
        warnings = []
        if s["perp_m"] > 150:
            warnings.append(f"Posisi Anda {s['perp_m']:.0f} m dari jalan terdekat; rute dimulai dari jalan itu.")
        H = self.wave(wave_m)
        pen: List[float] = []
        cap_view = None
        overrides: Dict[str, List[dict]] = {}
        high: Sequence[int] = ()
        if mode in ("aman", "gedung"):
            dests, high = self.mode_dests(mode, shelter_ids=shelter_ids, H=H, at=at, occupancy=occupancy)
            # Kedua mode: zona aman SELALU tujuan sah (sama dengan tabel HP & alokasi). Dulu mode gedung tanpa zona aman.
            include_safe = True
            blocked_nodes: Set[int] = set()
            pen, overrides, _, cap_view = self.mode_allocation(mode, dests, high, closures=closures, H=H,
                                                                budget_s=self.budget_s(eta_min), alloc=alloc)
        else:
            dests = self.point_dests(points, alert_active)
            include_safe = False
            blocked_nodes = self.hazard_nodes() if alert_active else set()
        pen = pen or [0.0] * len(dests)
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
            for node, c in self._place_target(d["edge"], d["t"], d["perp_m"], group, d["extra_s"] + pen[di]):
                targets.setdefault(node, []).append((di, c))
        if mode == "gedung":
            for i in high:  # semua tanah tinggi darurat dihitung sebagai satu tujuan
                targets.setdefault(i, []).append((HIGH_GROUND, 0.0))
        cap_out = {"status": cap_view["status"], "messages": cap_view.get("messages", [])} if cap_view else None
        ov = self._override_at(s, overrides.get(group, []))
        if ov is not None:
            r = self._override_route(lat, lon, s, ov, dests, pen, H, device_key, warnings)
            r["capacity"] = cap_out
            return r
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
                dest = _public_dest(d, pen[did])
                total -= pen[did]  # penalti kapasitas hanya untuk memilih tujuan, bukan waktu tempuh
            elif did == HIGH_GROUND:
                node = path[-1]
                dest = {"type": "tanah_tinggi", "name": "Tanah lebih tinggi", "lat": self.lat[node],
                        "lon": self.lon[node], "elev": self.elev[node],
                        "min_elev": round(H + float(self.params.get("dem_uncertainty_m", 5.0)), 1)}
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
            return {"ok": False, "reason": reason, "snapped": _ll(s), "warnings": warnings, "capacity": cap_out}
        return {"ok": True, "already_safe": False, "snapped": _ll(s), "warnings": warnings, "routes": routes,
                "capacity": cap_out}

    OVERRIDE_RADIUS_M = 25.0

    def _override_at(self, s: dict, recs: List[dict]) -> Optional[dict]:
        """Asal (bangunan) dengan alokasi khusus di posisi ini: ruas sama & dalam 25 m sepanjang ruas; bila ada
        beberapa, yang TERDEKAT (seri: urutan daftar). Sama dengan core.js overrideAt."""
        best, bd = None, None
        L = self.elen[s["edge"]]
        for r in recs:
            if r["e"] == s["edge"]:
                d = abs(r["t"] - s["t"]) * L
                if d <= self.OVERRIDE_RADIUS_M and (bd is None or d < bd):
                    best, bd = r, d
        return best

    def _override_route(self, lat, lon, s, rec, dests, pen, H, device_key, warnings) -> dict:
        if rec["unres"]:
            warnings.append(f"Kapasitas tujuan di sekitar Anda tidak cukup untuk {rec['unres']} orang dari bangunan ini: "
                            "tetap bergerak ke tempat tertinggi terdekat dan ikuti arahan petugas.")
        if not rec["opts"]:
            return {"ok": False, "reason": "Tidak ada tujuan yang masih memiliki kapasitas dan terjangkau dari sini.",
                    "snapped": _ll(s), "warnings": warnings, "allocation": {"origin": rec["o"], "unresolved": rec["unres"]}}
        counts = [o["n"] for o in rec["opts"]]
        i = AL.pick_option(device_key, rec["o"], counts) if device_key else max(range(len(counts)), key=lambda j: counts[j])
        opt = rec["opts"][i]
        nodes = opt["nodes"]
        coords = [[lon, lat], [s["lon"], s["lat"]]] + [[self.lon[x], self.lat[x]] for x in nodes]
        if opt["d"] >= 0:
            d = dests[opt["d"]]
            coords.append([d["lon"], d["lat"]])
            dest = _public_dest(d, pen[opt["d"]])
        else:
            node = nodes[-1]
            typ = "safe_zone" if self.safe[node] else "tanah_tinggi"
            dest = {"type": typ, "name": "Zona aman" if typ == "safe_zone" else "Tanah lebih tinggi",
                    "lat": self.lat[node], "lon": self.lon[node], "elev": self.elev[node]}
        return {"ok": True, "already_safe": False, "snapped": _ll(s), "warnings": warnings,
                "allocation": {"origin": rec["o"], "option": i, "shares": counts, "unresolved": rec["unres"]},
                "routes": [{"dest": dest, "time_s": opt["s"], "distance_m": round(_polyline_length(coords), 1),
                            "coords": [[round(c[0], 7), round(c[1], 7)] for c in coords]}]}

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
             candidate_ids: Optional[Sequence[str]] = None, design_height_m: Optional[float] = None,
             at=tsunami.WORST_CASE) -> dict:
        """Perencanaan dengan model alokasi yang SAMA dengan rute warga (allocate: LP leksikografis berkapasitas).

        at = waktu skenario: AlertTime atau WORST_CASE (bawaan: gedung berjam buka terbatas dianggap tutup).
        "Tidak sempat" = orang yang tujuan alokasinya ditempuh > anggaran waktu, atau tidak mendapat tujuan.
        Rekomendasi gedung tambahan: pemilihan greedy (HEURISTIK, ditandai) — angka akhir & kurva dihitung ulang
        dengan alokasi LP yang sama.
        """
        closures = closures or set()
        fz = frozenset(closures)
        H = self.wave(design_height_m)
        sc = {b["id"]: self.assess(b, H, at) for b in self.buildings}
        registered = set(shelter_ids)
        budget = max(0.0, (eta_min - delay_min - margin_min) * 60.0)
        nat_time = {g: [] for g in GROUPS}
        total_people = {g: 0.0 for g in GROUPS}
        red_before = {g: 0.0 for g in GROUPS}
        dem = {bi: q for bi, q in self.demand()}
        for g in GROUPS:
            dist, _, _ = self._natural(g, fz)
            for bi, b in enumerate(self.buildings):
                tt = self._building_time(b, g, dist)
                nat_time[g].append(tt)
                n = dem.get(bi, {}).get(g, 0)
                total_people[g] += n
                if n > 0 and tt > budget:
                    red_before[g] += n

        def cap_of(sid: str) -> float:
            b, a = self.buildings[self.bindex[sid]], sc[sid]
            if sid in registered:  # shelter resmi: hanya bila terverifikasi & terbuka (sama dengan rute warga)
                return float(a["capacity"]) if tsunami.registered_ok(b, a) else 0.0
            return float(a["capacity"]) if a["suitable"] else 0.0

        reg_dests = self.shelter_dests([sid for sid in shelter_ids if sid in self.bindex], H, at)

        def run(dests: List[dict]) -> Tuple[dict, Dict[Tuple[int, str], float], Dict[str, Dict[str, float]]]:
            out = self.allocate("plan", dests, closures=closures, high=(), H=H, budget_s=budget)
            res = out["result"]
            remaining: Dict[Tuple[int, str], float] = {}
            by_dest: Dict[str, Dict[str, float]] = {d["id"]: {g: 0.0 for g in GROUPS} for d in dests}
            origins = out["origins"]
            for oi, f in res["flows"].items():
                if oi >= len(origins):
                    continue
                bi, g, cands, fb = origins[oi]
                for d, n in f.items():
                    if n <= 0:
                        continue
                    c = INF if d == AL.UNRESOLVED else (fb if d == AL.UNLIMITED else cands[d])
                    if c > budget:
                        remaining[(bi, g)] = remaining.get((bi, g), 0.0) + n
                    if d >= 0:
                        by_dest[dests[d]["id"]][g] += n
            return out, remaining, by_dest

        out0, remaining, by_reg = run(reg_dests)
        shelters_out = []
        for sid in shelter_ids:
            if sid not in self.bindex:
                continue
            b = self.buildings[self.bindex[sid]]
            bg = by_reg.get(sid, {g: 0.0 for g in GROUPS})
            shelters_out.append(_shelter_summary(b, sum(bg.values()), bg, "terdaftar", sc[sid], cap_of(sid),
                                                 tsunami.registered_status(b, sc[sid])[1]))
        red_after_base = {g: sum(v for (bi, gg), v in remaining.items() if gg == g) for g in GROUPS}

        # --- rekomendasi (heuristik greedy pada sisa "tidak sempat"), lalu dihitung ulang dengan LP ---
        reach_cache: Dict[Tuple[str, str], List[Tuple[float, int]]] = {}

        def reach(sid: str, g: str) -> List[Tuple[float, int]]:
            key = (sid, g)
            if key not in reach_cache:
                ok = cap_of(sid) > 0 and sc[sid]["climb_s"] < budget
                reach_cache[key] = self._shelter_reach(sid, budget, g, closures, H) if ok else []
            return reach_cache[key]

        def gain_of(sid: str, rem: Dict[Tuple[int, str], float], commit: bool) -> float:
            cap, used = cap_of(sid), 0.0
            entries = sorted((tt, bi, g) for g in GROUPS for tt, bi in reach(sid, g) if rem.get((bi, g), 0) > 0)
            for tt, bi, g in entries:
                if used >= cap:
                    break
                take = min(cap - used, rem[(bi, g)])
                used += take
                if commit:
                    rem[(bi, g)] -= take
            return used

        picked: List[str] = []
        if optimize_k > 0:
            cands = [b["id"] for b in self.buildings if sc[b["id"]]["suitable"] and b["id"] not in registered]
            if candidate_ids:
                allowed = set(candidate_ids)
                cands = [c for c in cands if c in allowed]
            rem = dict(remaining)
            for _ in range(optimize_k):
                best = None
                for sid in cands:
                    gain = gain_of(sid, rem, False)
                    if gain <= 0:
                        continue
                    key = (gain, -sc[sid]["climb_s"])
                    if best is None or key > best[0]:
                        best = (key, sid)
                if best is None:
                    break
                gain_of(best[1], rem, True)
                cands.remove(best[1])
                picked.append(best[1])

        def rec_dest(sid: str) -> dict:
            b = self.buildings[self.bindex[sid]]
            return self._bdest(b, sc[sid], "gedung")

        curve = [{"k": 0, "red_total": round(sum(red_after_base.values()))}]
        out_final, rem_final, by_final = out0, remaining, by_reg
        for k in range(1, len(picked) + 1):
            out_final, rem_final, by_final = run(reg_dests + [rec_dest(x) for x in picked[:k]])
            curve.append({"k": k, "red_total": round(sum(rem_final.values()))})
        recommended = []
        for sid in picked:
            b = self.buildings[self.bindex[sid]]
            bg = by_final.get(sid, {g: 0.0 for g in GROUPS})
            r = _shelter_summary(b, sum(bg.values()), bg, "rekomendasi", sc[sid], cap_of(sid))
            # Rekomendasi = skenario "bila didaftarkan". Gedung Perlu Verifikasi: angka hanya berlaku SETELAH lolos survei.
            r["requires_verification"] = sc[sid]["tier"] != "verified"
            recommended.append(r)
        red_after = {g: sum(v for (bi, gg), v in rem_final.items() if gg == g) for g in GROUPS}
        bstat = []
        for bi, b in enumerate(self.buildings):
            q = dem.get(bi)
            if not q:
                bstat.append(None)
                continue
            red0 = sum(q[g] for g in GROUPS if nat_time[g][bi] > budget)
            red1 = sum(rem_final.get((bi, g), 0.0) for g in GROUPS)
            if red0 <= 0 and red1 <= 0:
                st = "sempat"
            elif red1 <= 0:
                st = "terlayani_shelter"
            elif red1 < red0:
                st = "sebagian"
            else:
                st = "tidak_sempat"
            bstat.append(st)
        suit = [b["id"] for b in self.buildings if sc[b["id"]]["suitable"]]
        dest_ids = [i for i in suit if sc[i]["destination"]]
        # Daftar verifikasi BPBD: lolos lantai aman konservatif tetapi belum terverifikasi (urut kapasitas terbesar).
        vlist = sorted((i for i in suit if sc[i]["tier"] == "unverified"), key=lambda i: -sc[i]["capacity"])
        verification_list = [{"id": i, "name": self.buildings[self.bindex[i]]["name"] or
                              f"Gedung {self.buildings[self.bindex[i]]['levels']} lantai",
                              "levels": self.buildings[self.bindex[i]]["levels"], "min_floor": sc[i]["min_floor"],
                              "capacity_if_verified": sc[i]["capacity"], "caveats": sc[i]["caveats"][:4]}
                             for i in vlist[:40]]
        closed = [b["id"] for b in self.buildings if sc[b["id"]]["closed_now"]]
        fin = out_final["result"]
        return {
            "budget_s": budget,
            "wave_m": H,
            "time": _time_info(tsunami._coerce_at(at)),
            "closed_ids": closed,
            "closed_count": len(closed),
            "suitable_ids": suit,
            "suitable_count": len(suit),
            "suitable_capacity": sum(sc[i]["capacity"] for i in suit),
            "destination_ids": dest_ids,
            "destination_count": len(dest_ids),
            "destination_capacity": sum(sc[i]["capacity"] for i in dest_ids),
            "verification_list": verification_list,
            "verification_count": len(vlist),
            # Gedung relevan (survey_prio) yang jumlah lantainya belum diketahui -> perlu survei/unggah data tinggi.
            "unknown_levels": sum(1 for b in self.buildings if b.get("survey_prio")
                                  and b.get("levels_src", "osm") == "default"),
            "total_people": {g: round(v) for g, v in total_people.items()},
            "red_before": {g: round(v) for g, v in red_before.items()},
            "red_after_registered": {g: round(v) for g, v in red_after_base.items()},
            "red_after": {g: round(v) for g, v in red_after.items()},
            "shelters": shelters_out,
            "recommended": recommended,
            "recommend_method": "greedy (heuristik) — angka setelahnya dihitung ulang dengan alokasi LP",
            "curve": curve,
            "allocation": {"status": out_final["status"], "method": fin["meta"]["method"],
                           "optimal": fin["meta"]["optimal"], "totals": {k: v for k, v in fin["totals"].items()
                                                                          if k != "time_sum"},
                           "timing": fin["meta"]["timing"], "messages": fin["meta"]["messages"],
                           "errors": (fin["errors"] + out_final["table_errors"])[:10],
                           "population": self.demand_info()},
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

def _compat_check(out: Optional[dict], dests: List[dict]) -> dict:
    """Bentuk lama `capacity_check` (kompatibel): overflow = gedung yang beban HP-nya melebihi kapasitas."""
    if out is None or out["status"] == "gagal":
        return {"overflow": [], "load": {}, "computed": False}
    load = out["table_load"]
    return {"overflow": [d["id"] for i, d in enumerate(dests) if load[i] > int(d.get("capacity") or 0)],
            "load": {d["id"]: load[i] for i, d in enumerate(dests)}, "computed": True,
            "status": out["status"]}


def _pack(dist: List[float], nxt: List[int], dest: List[int]) -> dict:
    return {
        "time": [round(d) if math.isfinite(d) else -1 for d in dist],
        "next": nxt,
        "dest": [d if d >= -1 else -9 for d in dest],
    }


def _occ_fields(d: dict) -> dict:
    """Okupansi yang dilaporkan petugas: kapasitas desain & orang yang sudah di dalam (capacity = sisanya)."""
    return {"capacity_design": int(d["capacity_design"]), "occupied": int(d["occupied"])} if "occupied" in d else {}


def _public_dest(d: dict, pen: float = 0.0) -> dict:
    keep = ("type", "id", "name", "kind", "lat", "lon", "levels", "capacity", "occupied", "status", "services",
            "updated_at", "note", "verified", "surveyed", "min_floor", "depth_m", "min_floor_nominal",
            "depth_nominal_m", "elev_margin_m", "levels_src", "structure", "access",
            "open_hours", "open_days", "access_state", "elev_conf", "tier", "tier_label", "caveats")
    out = {k: d[k] for k in keep if k in d}
    if pen > 0:
        # Penalti kapasitas (detik): HP mengurangkannya dari waktu tabel agar waktu tempuh yang tampil tetap asli.
        out["pen_s"] = round(pen, 1)
        out["crowded"] = True
    return out


def _time_info(at) -> Optional[dict]:
    if at == tsunami.WORST_CASE:
        return {"mode": "terburuk", "label": "kondisi terburuk (gedung berjam buka terbatas dianggap tutup)"}
    if isinstance(at, tsunami.AlertTime):
        return {"mode": "jam", "ts": at.ts, "label": tsunami.local_time_label(at), "window_s": at.window_s,
                "tz_src": at.src}
    return None


def _shelter_summary(b: dict, used: float, by_g: Dict[str, float], role: str, a: dict, cap: float,
                     reason: str = "") -> dict:
    return {"id": b["id"], "name": b["name"] or f"Gedung {b['levels']} lantai", "kind": b["kind"],
            "levels": b["levels"], "capacity": int(cap), "assigned": round(used),
            "assigned_umum": round(by_g.get("umum", 0)), "assigned_rentan": round(by_g.get("rentan", 0)),
            "lat": b["lat"], "lon": b["lon"], "role": role, "min_floor": a["min_floor"], "depth_m": a["depth_m"],
            "usable": cap > 0, "reason": (reason or a["reason"]) if cap <= 0 else "",
            "levels_src": b.get("levels_src", "osm"), "surveyed": tsunami._surveyed(b),
            "min_floor_nominal": a.get("min_floor_nominal"), "elev_margin_m": a.get("elev_margin_m"),
            "tier": a["tier"], "tier_label": a.get("tier_label"), "caveats": a["caveats"],
            "access_state": a.get("access_state")}


def _ll(s: dict) -> dict:
    return {"lat": s["lat"], "lon": s["lon"], "perp_m": round(s["perp_m"], 1)}


def _polyline_length(coords: Sequence[Sequence[float]]) -> float:
    return sum(haversine_m(a[1], a[0], b[1], b[0]) for a, b in zip(coords[:-1], coords[1:]))
