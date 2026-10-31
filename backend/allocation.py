"""Alokasi evakuasi berkapasitas (masalah transportasi) — modul murni, tanpa graf jalan.

Lihat docs/capacity/DESIGN.md untuk rumusan lengkap. Ringkas:

    untuk setiap asal o:     Σ_d x_od + y_o + z_o = q_o      (q_o bulat; pembukuan tepat)
    untuk setiap gedung d:   Σ_o x_od ≤ κ_d                  (kapasitas KERAS)
    x_od hanya untuk tujuan layak & terjangkau; y_o (zona aman/tanah tinggi, kapasitas tidak diketahui) hanya bila terjangkau.

Objektif leksikografis: (1) min orang tidak teralokasi, (2) min orang yang tiba setelah batas waktu T
(prioritize_deadline=True, bawaan; dilewati bila T tidak ada), (3) min total waktu tempuh. Tahap (2) membuat lebih
banyak asal tidak terwakili tabel HP sehingga dikirim sebagai overrides (lihat docs/capacity/DESIGN.md §2 dan
BENCHMARK.md). Solver: HiGHS lewat scipy (bila tersedia), cadangan greedy berbasis urgensi.
Hasil akhir selalu bulat dan divalidasi ulang secara independen oleh `validate`.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

INF = float("inf")
UNLIMITED = -1      # indeks tujuan untuk zona aman / tanah tinggi (kapasitas tidak diketahui)
UNRESOLVED = -2     # orang yang tidak mendapat tujuan
LP_METHOD = "highs-ipm"  # HiGHS interior point + crossover (solusi basis); lihat docs/capacity/BENCHMARK.md

REASON_NO_DEST = "tidak_ada_tujuan_terjangkau"
REASON_CAPACITY = "kapasitas_habis"

try:  # scipy opsional: tanpa scipy dipakai cadangan greedy (dilaporkan sebagai bukan optimal)
    import numpy as _np
    from scipy.optimize import linprog as _linprog
    from scipy.sparse import csr_matrix as _csr
    HAVE_LP = True
except Exception:  # pragma: no cover - lingkungan tanpa scipy
    HAVE_LP = False


@dataclass
class Origin:
    """Satu asal permintaan: penghuni satu bangunan dari satu kelompok kecepatan."""
    key: str                   # mis. "w123|umum"
    demand: int                # orang (bulat, >= 0)
    cands: Dict[int, float]    # indeks tujuan berkapasitas -> waktu tempuh (s)
    fallback: float = INF      # waktu ke zona aman/tanah tinggi terdekat (INF = tidak terjangkau)
    info: dict = field(default_factory=dict)


@dataclass
class Problem:
    origins: List[Origin]
    dests: List[dict]          # setiap tujuan berkapasitas: {"id", "capacity" (int >= 0), ...}
    deadline_s: Optional[float] = None


def _cap(d: dict) -> int:
    c = d.get("capacity")
    return max(0, int(c)) if c is not None else 0


def _late(c: float, T: Optional[float]) -> bool:
    return T is not None and c > T + 1e-9


# ---------------------------------------------------------------------------
# Validasi independen
# ---------------------------------------------------------------------------
def validate(problem: Problem, flows: Dict[int, Dict[int, int]]) -> List[str]:
    """Periksa ulang alokasi akhir. flows[o][d] = orang (d = indeks gedung, UNLIMITED, UNRESOLVED).

    Dihitung dari daftar alokasi itu sendiri, bukan dari keluaran solver.
    """
    errs: List[str] = []
    load = [0] * len(problem.dests)
    for oi, o in enumerate(problem.origins):
        f = flows.get(oi, {})
        tot = 0
        for d, n in f.items():
            if not isinstance(n, int) or n < 0:
                errs.append(f"{o.key}: jumlah tidak bulat/negatif ({d}: {n!r})")
                continue
            tot += n
            if n == 0:
                continue
            if d == UNLIMITED:
                if not math.isfinite(o.fallback):
                    errs.append(f"{o.key}: dialokasikan ke zona aman yang tidak terjangkau")
            elif d == UNRESOLVED:
                pass
            elif 0 <= d < len(problem.dests):
                if d not in o.cands or not math.isfinite(o.cands[d]):
                    errs.append(f"{o.key}: tujuan {problem.dests[d].get('id')} tidak layak/terjangkau dari asal ini")
                load[d] += n
            else:
                errs.append(f"{o.key}: indeks tujuan tidak dikenal {d}")
        if tot != o.demand:
            errs.append(f"{o.key}: alokasi {tot} != permintaan {o.demand}")
    for d, dd in enumerate(problem.dests):
        if load[d] > _cap(dd):
            errs.append(f"gedung {dd.get('id')}: {load[d]} orang > kapasitas {_cap(dd)}")
    return errs


# ---------------------------------------------------------------------------
# Ringkasan
# ---------------------------------------------------------------------------
def summarize(problem: Problem, flows: Dict[int, Dict[int, int]]) -> dict:
    T = problem.deadline_s
    load = [0] * len(problem.dests)
    tot = {"demand": 0, "to_shelter": 0, "to_unlimited": 0, "unresolved": 0, "late": 0, "time_sum": 0.0,
           "time_max": 0.0}
    unres_reason = {REASON_NO_DEST: 0, REASON_CAPACITY: 0}
    for oi, o in enumerate(problem.origins):
        tot["demand"] += o.demand
        for d, n in flows.get(oi, {}).items():
            if n <= 0:
                continue
            if d == UNRESOLVED:
                tot["unresolved"] += n
                no_dest = not o.cands and not math.isfinite(o.fallback)
                unres_reason[REASON_NO_DEST if no_dest else REASON_CAPACITY] += n
                continue
            c = o.fallback if d == UNLIMITED else o.cands[d]
            if d == UNLIMITED:
                tot["to_unlimited"] += n
            else:
                tot["to_shelter"] += n
                load[d] += n
            tot["time_sum"] += n * c
            tot["time_max"] = max(tot["time_max"], c)
            if _late(c, T):
                tot["late"] += n
    alloc = tot["to_shelter"] + tot["to_unlimited"]
    tot["time_mean"] = tot["time_sum"] / alloc if alloc else None
    tot["unresolved_by_reason"] = unres_reason
    caps = [_cap(d) for d in problem.dests]
    tot["capacity_total"] = sum(caps)
    tot["capacity_used"] = sum(load)
    return {"totals": tot, "load": load, "remaining": [c - l for c, l in zip(caps, load)]}


# ---------------------------------------------------------------------------
# Cadangan greedy (tanpa scipy / solver gagal)
# ---------------------------------------------------------------------------
def greedy(problem: Problem, residual: Optional[Dict[int, int]] = None,
           cap_left: Optional[List[int]] = None) -> Dict[int, Dict[int, int]]:
    """Alokasi greedy berbasis urgensi. Kapasitas tetap keras; TIDAK optimal.

    Urutan: asal dengan pilihan tepat waktu paling sedikit lebih dulu, lalu "penyesalan" terbesar (selisih tujuan
    termurah kedua dan pertama). Setiap orang ke tujuan termurah yang masih ada kapasitas, lalu zona aman, lalu tidak
    teralokasi.
    """
    T = problem.deadline_s
    caps = list(cap_left) if cap_left is not None else [_cap(d) for d in problem.dests]
    need = residual if residual is not None else {i: o.demand for i, o in enumerate(problem.origins)}

    def urgency(oi: int):
        o = problem.origins[oi]
        opts = sorted(list(o.cands.values()) + ([o.fallback] if math.isfinite(o.fallback) else []))
        on_time = sum(1 for c in opts if not _late(c, T))
        regret = (opts[1] - opts[0]) if len(opts) > 1 else INF
        return (on_time, -regret if math.isfinite(regret) else -1e18, o.key)

    flows: Dict[int, Dict[int, int]] = {}
    for oi in sorted((i for i, n in need.items() if n > 0), key=urgency):
        o = problem.origins[oi]
        left = need[oi]
        f = flows.setdefault(oi, {})
        for d, c in sorted(o.cands.items(), key=lambda kv: (_late(kv[1], T), kv[1])):
            if left <= 0:
                break
            if math.isfinite(o.fallback) and o.fallback <= c and not (_late(o.fallback, T) and not _late(c, T)):
                break  # zona aman sama cepat/lebih cepat: tidak memakai kursi gedung
            take = min(left, caps[d])
            if take > 0:
                f[d] = f.get(d, 0) + take
                caps[d] -= take
                left -= take
        if left > 0:
            if math.isfinite(o.fallback):
                f[UNLIMITED] = f.get(UNLIMITED, 0) + left
            else:
                f[UNRESOLVED] = f.get(UNRESOLVED, 0) + left
    return flows


# ---------------------------------------------------------------------------
# LP leksikografis (HiGHS)
# ---------------------------------------------------------------------------
class _LP:
    """Satu masalah transportasi dengan kolom x (gedung), y (zona aman), z (tidak teralokasi) per asal."""

    def __init__(self, problem: Problem, demand: Sequence[int], caps: Sequence[int]):
        self.p = problem
        T = problem.deadline_s
        oi_l, d_l, c_l = [], [], []
        for oi, o in enumerate(problem.origins):
            if demand[oi] <= 0:
                continue
            for d, c in o.cands.items():
                if math.isfinite(c):
                    oi_l.append(oi); d_l.append(d); c_l.append(c)
            if math.isfinite(o.fallback):
                oi_l.append(oi); d_l.append(UNLIMITED); c_l.append(o.fallback)
            oi_l.append(oi); d_l.append(UNRESOLVED); c_l.append(0.0)
        self.oi = _np.array(oi_l, dtype=_np.int64)
        self.d = _np.array(d_l, dtype=_np.int64)
        self.c = _np.array(c_l, dtype=float)
        self.is_z = self.d == UNRESOLVED
        self.late = (~self.is_z) & (self.c > T + 1e-9) if T is not None else _np.zeros(len(self.c), bool)
        rows = sorted(set(self.oi.tolist()))
        self.rows = rows
        row_of = {o: r for r, o in enumerate(rows)}
        n = len(self.c)
        self.A_eq = _csr((_np.ones(n), (_np.array([row_of[o] for o in self.oi]), _np.arange(n))), shape=(len(rows), n))
        self.b_eq = _np.array([demand[o] for o in rows], dtype=float)
        lim = self.d >= 0
        self.A_cap = _csr((_np.ones(int(lim.sum())), (self.d[lim], _np.arange(n)[lim])), shape=(len(caps), n))
        self.b_cap = _np.array(caps, dtype=float)
        self.locks: List[Tuple[_np.ndarray, float]] = []
        self.z_fixed = False

    def solve(self, cost, time_limit: float):
        A_ub = [self.A_cap]
        b_ub = [self.b_cap]
        for mask, bound in self.locks:
            A_ub.append(_csr(mask.astype(float).reshape(1, -1)))
            b_ub.append(_np.array([bound]))
        from scipy.sparse import vstack
        ub = _np.full(len(self.c), _np.inf)
        if self.z_fixed:
            ub[self.is_z] = 0.0  # semua asal punya tujuan tanpa batas: tidak ada yang boleh tidak teralokasi
        res = _linprog(cost, A_ub=vstack(A_ub).tocsr(), b_ub=_np.concatenate(b_ub), A_eq=self.A_eq, b_eq=self.b_eq,
                       bounds=_np.column_stack([_np.zeros(len(self.c)), ub]), method=LP_METHOD,
                       options={"time_limit": max(1.0, time_limit)})
        return res

    def lock(self, mask, value: float):
        # toleransi kecil di atas optimum tahap sebelumnya (angka orang bulat -> 1e-6 aman)
        self.locks.append((mask, value + 1e-6 * max(1.0, abs(value))))


def _round_flows(problem: Problem, lp: _LP, x, caps: Sequence[int]) -> Tuple[Dict[int, Dict[int, int]], dict]:
    """Rekonsiliasi pecahan -> bulat: lantai + masalah transportasi sisa (unimodular total)."""
    flows: Dict[int, Dict[int, int]] = {}
    fl = _np.floor(x + 1e-9).astype(_np.int64)
    used = [0] * len(caps)
    assigned = {}
    for j in _np.nonzero(fl > 0)[0]:
        oi, d = int(lp.oi[j]), int(lp.d[j])
        n = int(fl[j])
        f = flows.setdefault(oi, {})
        f[d] = f.get(d, 0) + n
        assigned[oi] = assigned.get(oi, 0) + n
        if d >= 0:
            used[d] += n
    residual = {oi: o.demand - assigned.get(oi, 0) for oi, o in enumerate(problem.origins)
                if o.demand - assigned.get(oi, 0) > 0}
    info = {"fractional_vars": int(_np.sum(_np.abs(x - _np.round(x)) > 1e-6)), "residual_people": sum(residual.values()),
            "method": "tidak_perlu"}
    if not residual:
        return flows, info
    cap_left = [max(0, int(c) - u) for c, u in zip(caps, used)]
    sub = Problem([problem.origins[oi] for oi in residual], problem.dests, problem.deadline_s)
    dem = [residual[oi] for oi in residual]
    ok = False
    try:
        lp2 = _LP(sub, dem, cap_left)
        R = float(sum(dem))
        cmax = float(max([1.0] + [c for c in lp2.c if math.isfinite(c)]))
        # Bobot leksikografis yang DITURUNKAN dari data (bukan konstanta sembarang): satu orang lebih sedikit
        # terlambat > seluruh selisih waktu (R*cmax); satu orang lebih sedikit tak teralokasi > seluruh selisih
        # terlambat + waktu (R*(w_late+cmax)).
        w_late = R * cmax + 1.0
        w_unres = R * (w_late + cmax) + 1.0
        cost = lp2.c + w_late * lp2.late + w_unres * lp2.is_z
        res = lp2.solve(cost, 30.0)
        if res.status == 0 and _np.all(_np.abs(res.x - _np.round(res.x)) < 1e-6):
            xr = _np.round(res.x).astype(_np.int64)
            orig = list(residual)
            for j in _np.nonzero(xr > 0)[0]:
                oi, d = orig[int(lp2.oi[j])], int(lp2.d[j])
                f = flows.setdefault(oi, {})
                f[d] = f.get(d, 0) + int(xr[j])
            ok = True
            info["method"] = "lp_sisa"
    except Exception as exc:  # pragma: no cover - dicatat
        info["error"] = str(exc)[:200]
    if not ok:
        g = greedy(problem, residual=residual, cap_left=cap_left)
        for oi, f2 in g.items():
            f = flows.setdefault(oi, {})
            for d, n in f2.items():
                f[d] = f.get(d, 0) + n
        info["method"] = "greedy_sisa"
    return flows, info


def solve(problem: Problem, *, time_limit_s: float = 60.0,
          price_check: Optional[Callable[[List[float], dict], int]] = None, max_price_rounds: int = 5,
          use_lp: bool = True, prioritize_deadline: bool = True) -> dict:
    """Alokasi leksikografis. price_check(duals, meta) boleh menambah kandidat ke problem.origins[*].cands dan
    mengembalikan jumlah kolom baru (pemeriksaan harga / column generation)."""
    t_all = time.perf_counter()
    caps = [_cap(d) for d in problem.dests]
    demand = [o.demand for o in problem.origins]
    meta = {"method": None, "optimal": False, "stages": [], "price_rounds": 0, "price_converged": None,
            "objective": ["min_tidak_teralokasi"] + (["min_terlambat"] if prioritize_deadline else []) + ["min_total_waktu"],
            "messages": [], "timing": {}}
    flows: Optional[Dict[int, Dict[int, int]]] = None
    duals = [0.0] * len(problem.dests)
    if use_lp and HAVE_LP and sum(demand) > 0:
        try:
            flows, duals = _solve_lp(problem, demand, caps, time_limit_s, price_check, max_price_rounds, meta,
                                     prioritize_deadline)
        except Exception as exc:  # solver gagal / batas waktu: cadangan greedy, dilaporkan
            meta["messages"].append(f"LP gagal: {str(exc)[:200]}; memakai greedy")
            flows = None
    if flows is None:
        t0 = time.perf_counter()
        flows = greedy(problem)
        meta["method"] = "greedy" if sum(demand) > 0 else "kosong"
        meta["optimal"] = sum(demand) == 0
        if not HAVE_LP:
            meta["messages"].append("scipy tidak terpasang: alokasi greedy (kapasitas keras, tidak optimal)")
        meta["timing"]["greedy_s"] = round(time.perf_counter() - t0, 3)
        duals = [0.0] * len(problem.dests)
    t0 = time.perf_counter()
    errs = validate(problem, flows)
    meta["timing"]["validate_s"] = round(time.perf_counter() - t0, 3)
    s = summarize(problem, flows)
    meta["timing"]["total_s"] = round(time.perf_counter() - t_all, 3)
    status = "gagal" if errs else ("kurang" if s["totals"]["unresolved"] > 0 else "ok")
    return {"status": status, "errors": errs[:20], "flows": flows, "duals": duals, **s, "meta": meta}


def _solve_lp(problem, demand, caps, time_limit_s, price_check, max_rounds, meta, prioritize_deadline=False):
    t_start = time.perf_counter()
    T = problem.deadline_s
    rounds = 0
    while True:
        lp = _LP(problem, demand, caps)
        need_z = any(d > 0 and not math.isfinite(o.fallback) for o, d in zip(problem.origins, demand))
        stages = []
        t_solve = time.perf_counter()
        if need_z:
            res = lp.solve(lp.is_z.astype(float), time_limit_s)
            _check(res, "tahap 1 (tidak teralokasi)")
            stages.append(("tidak_teralokasi", float(res.fun)))
            lp.lock(lp.is_z, float(res.fun))
        else:
            lp.z_fixed = True
        if prioritize_deadline and T is not None and bool(lp.late.any()):
            res = lp.solve(lp.late.astype(float), time_limit_s)
            _check(res, "tahap 2 (terlambat)")
            stages.append(("terlambat", float(res.fun)))
            lp.lock(lp.late, float(res.fun))
        res = lp.solve(_np.where(lp.is_z, 0.0, lp.c), time_limit_s)
        _check(res, "tahap 3 (waktu tempuh)")
        stages.append(("total_waktu_s", float(res.fun)))
        meta["timing"]["solve_s"] = round(meta["timing"].get("solve_s", 0.0) + time.perf_counter() - t_solve, 3)
        marg = -_np.asarray(res.ineqlin.marginals)
        duals = [max(0.0, float(v)) for v in marg[:len(caps)]]
        lock_duals = [max(0.0, float(v)) for v in marg[len(caps):]]
        if price_check is None or rounds >= max_rounds:
            if price_check is not None:
                meta["price_converged"] = False
                meta["messages"].append("Pemeriksaan harga belum konvergen: hasil optimal hanya pada kandidat")
            break
        t0 = time.perf_counter()
        added = price_check(duals, {"eq_duals": list(_np.asarray(res.eqlin.marginals)), "rows": lp.rows,
                                    "lock_duals": lock_duals, "deadline_s": T})
        meta["timing"]["price_s"] = round(meta["timing"].get("price_s", 0.0) + time.perf_counter() - t0, 3)
        rounds += 1
        if not added:
            meta["price_converged"] = True
            break
        if time.perf_counter() - t_start > time_limit_s:
            meta["price_converged"] = False
            meta["messages"].append("Batas waktu pemeriksaan harga tercapai")
            break
    meta["price_rounds"] = rounds
    meta["stages"] = [{"tahap": a, "nilai": round(b, 3)} for a, b in stages]
    t0 = time.perf_counter()
    flows, rinfo = _round_flows(problem, lp, res.x, caps)
    meta["timing"]["reconcile_s"] = round(time.perf_counter() - t0, 3)
    meta["reconcile"] = rinfo
    meta["method"] = "lp_highs"
    meta["optimal"] = (meta["price_converged"] is not False) and rinfo["method"] in ("tidak_perlu", "lp_sisa")
    meta["columns"] = int(len(lp.c))
    return flows, duals


def _check(res, what: str) -> None:
    if res.status != 0:
        raise RuntimeError(f"{what}: {res.message}")


# ---------------------------------------------------------------------------
# Pembulatan penduduk model menjadi orang (bulat), total dipertahankan
# ---------------------------------------------------------------------------
def largest_remainder(values: Sequence[float], total: Optional[int] = None) -> List[int]:
    """Bulatkan daftar nilai >= 0 sehingga jumlahnya = total (bawaan: round(Σ)), selisih per elemen < 1."""
    vals = [max(0.0, float(v)) for v in values]
    if total is None:
        total = int(round(sum(vals)))
    base = [int(math.floor(v)) for v in vals]
    rest = total - sum(base)
    order = sorted(range(len(vals)), key=lambda i: (-(vals[i] - base[i]), i))
    for i in order[:max(0, rest)]:
        base[i] += 1
    return base


def pick_option(key: str, origin_key: str, counts: Sequence[int]) -> int:
    """Pilih opsi asal yang dipecah dengan hash stabil (FNV-1a 32-bit) — SAMA dengan core.js pickOption."""
    tot = sum(counts)
    if tot <= 0:
        return 0
    h = 0x811C9DC5
    for ch in f"{key}|{origin_key}".encode("utf-8"):
        h ^= ch
        h = (h * 0x01000193) & 0xFFFFFFFF
    u = h / 4294967296.0 * tot
    acc = 0
    for i, n in enumerate(counts):
        acc += n
        if u < acc:
            return i
    return len(counts) - 1
