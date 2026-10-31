"""API dan server web SEMPAT.

Satu server melayani banyak area:
- area desa yang disiapkan (tools/prepare_area.py) atau desa sintetis untuk uji,
- area "live" yang dibangun otomatis dari OpenStreetMap di sekitar posisi warga mana pun.
"""
from __future__ import annotations

import asyncio
import collections
import contextlib
import datetime as _dt
import hashlib
import json
import math
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (__version__, area_builder, auth, evidence, mapgrid, population, bmkg, config, guidance, heights, live_area, osm_fetch, tsunami,
               webpush)
from .engine import POINT_STATUSES, Engine
from .events import Broker
from .geo import haversine_m
from .store import Store, now_iso

POINT_KINDS = {
    "posko_sar": "Posko SAR",
    "titik_kumpul": "Titik kumpul",
    "posko_medis": "Posko medis",
    "dapur_umum": "Dapur umum",
    "pengungsian": "Tempat pengungsian",
}
SERVICES = ("medis", "air", "makanan", "tempat_tidur", "listrik", "komunikasi")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _parse_iso(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        d = _dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.timestamp()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Area yang dimuat di memori
# ---------------------------------------------------------------------------
class AreaCtx:
    def __init__(self, area: dict, grids: Sequence[dict] = ()):
        self.area = area_builder.upgrade_area(area)
        self.id = area["meta"]["id"]
        self.meta = area["meta"]
        # Penduduk: grid regional yang mencakup area (bila ada), selain itu model lama berlabel jelas.
        self.grids = list(grids)
        population.apply_grids(self.area, self.grids, population.edge_snapper(self.area))
        # Zona bahaya untuk peta: poligon resmi, atau sel PERKIRAAN aturan (berlabel; lihat mapgrid.hazard_view).
        self.area["hazard_view"] = mapgrid.hazard_view(self.area)
        self.engine = Engine(self.area)
        self.lock = threading.RLock()  # reentrant: serialisasi malas bisa diminta dari dalam blok `with c.lock`
        self.refresh_bytes()
        self.tables_bytes = b"{}"
        self.version = 0
        # Zona waktu area (eksplisit/provinsi/perencana; cadangan: bujur) untuk jam buka gedung saat peringatan.
        self.tz = tsunami.area_timezone(self.meta)
        # Tanpa peringatan tabel dihitung dengan jam sekarang: hitung ulang saat status buka/tutup gedung berubah.
        self.valid_until: Optional[float] = None
        self.orphans = {"surveys": [], "shelters": []}
        self.alloc_requests: List[dict] = []  # skenario alokasi yang belum dihitung (diisi recompute)
        self.used = time.time()
        self.facilities = [dict(f, status="osm", services=[], capacity=None, verified=False, updated_at=None)
                           for f in area.get("facilities") or []]

    def refresh_bytes(self, changed: Optional[Sequence[str]] = None) -> None:
        """Tandai data area berubah. area_bytes = data lengkap (internal, dasar penyimpanan); staff_bytes = petugas/
        perencana; public_bytes = versi warga yang ringkas: hanya gedung relevan (bertingkat / bukan rumah tunggal /
        sudah disurvei) dengan atribut yang dipakai HP, koordinat 6 desimal, tanpa atribut debug elevasi, penduduk per
        bangunan, dan asal virtual.

        Serialisasi dibuat MALAS (saat pertama diminta) lalu disimpan, jadi survei beruntun tidak membangun ulang
        JSON belasan MB setiap kali. `changed` = ID gedung yang berubah (None = perubahan menyeluruh) dicatat per
        revisi supaya klien bisa mengambil tambalan kecil (GET /api/area/patch) alih-alih seluruh area."""
        if not hasattr(self, "changes"):
            # epoch: unik per pemuatan area di memori. Revisi dimulai lagi dari 0 setelah server dimulai ulang atau
            # area dikeluarkan dari memori, jadi klien hanya boleh menambal bila epoch-nya sama.
            self.epoch = secrets.token_hex(4)
            self.changes = collections.deque(maxlen=64)
        self.rev = getattr(self, "rev", -1) + 1
        self.changes.append((self.rev, None if changed is None else frozenset(changed)))
        self._bytes = {}

    def _cached(self, key: str, build) -> bytes:
        with self.lock:
            v = self._bytes.get(key)
            if v is None:
                v = self._bytes[key] = json.dumps(build(), separators=(",", ":")).encode()
            return v

    @property
    def area_bytes(self) -> bytes:
        return self._cached("full", lambda: dict(self.area, meta=dict(self.area["meta"], rev=self.rev, epoch=self.epoch)))

    @property
    def staff_bytes(self) -> bytes:
        return self._cached("staff", lambda: staff_area(self.area, self.rev, self.epoch))

    @property
    def public_bytes(self) -> bytes:
        return self._cached("public", lambda: public_area(self.area, self.rev, self.epoch))

    def changed_since(self, rev: int) -> Optional[set]:
        """ID gedung yang berubah setelah revisi `rev`; None bila tidak bisa dipastikan (perlu muat ulang penuh)."""
        if rev == self.rev:
            return set()
        if rev > self.rev or not self.changes or self.changes[0][0] > rev + 1:
            return None
        out: set = set()
        for r, ids in self.changes:
            if r <= rev:
                continue
            if ids is None:
                return None
            out |= ids
        return out

    def apply_survey(self, info: dict) -> Optional[dict]:
        """Timpa data gedung dengan hasil survei petugas (sumber paling dipercaya)."""
        i = self.engine.bindex.get(info["building_id"])
        if i is None:
            return None
        b = self.engine.buildings[i]
        if "_auto" not in b:  # simpan nilai otomatis agar survei bisa dibatalkan
            b["_auto"] = {k: b.get(k) for k in SURVEY_LAYER_KEYS}
        # Jumlah lantai menjadi "survei" HANYA bila petugas menyatakan menghitungnya langsung (audit D-7). Baris lama
        # (sebelum kolom levels_confirmed) dianggap belum dikonfirmasi: nilai perkiraan tetap perkiraan.
        if info.get("levels") and info.get("levels_confirmed"):
            b["levels"] = int(info["levels"])
            b["levels_src"] = "survey"
            b["levels_est"] = False
            b.pop("levels_note", None)
        # Elevasi tanah diukur petugas dengan metode yang diakui -> kepercayaan "tinggi" (margin 0 m).
        if info.get("ground_elev_m") is not None and info.get("ground_elev_method") in tsunami.ELEV_METHODS:
            b["elev"] = round(float(info["ground_elev_m"]), 2)
            b["elev_src"] = "survei"
            b["elev_method"] = info["ground_elev_method"]
            b["elev_conf"] = "tinggi"
            b["elev_uncertain"] = False
        b["condition"] = info.get("condition") or None
        b["basement"] = int(info.get("basement") or 0)
        if info.get("structure"):
            b["structure"] = info["structure"]
        b["access"] = info.get("access") or None  # tidak diisi = belum diketahui (TIDAK dianggap 24 jam)
        if b["access"] == "jam_kerja":
            b["open_hours"] = info.get("open_hours") or None  # baris lama tanpa jam buka -> dianggap terkunci
            b["open_days"] = info.get("open_days") or None
        else:
            b.pop("open_hours", None)
            b.pop("open_days", None)
        b["roof"] = bool(info.get("roof"))
        b["slf"] = bool(info.get("slf"))
        b.pop("verified", None)  # kunci lama: "sudah disurvei" kini `surveyed`; terverifikasi = tier (tsunami.verification)
        b["surveyed"] = True
        b["survey"] = {"by": info.get("updated_by"), "at": info.get("updated_at"), "note": info.get("note") or ""}
        heights._refresh_candidate(b, self.area["params"])
        return b

    def clear_survey(self, bid: str) -> None:
        i = self.engine.bindex.get(bid)
        if i is None:
            return
        b = self.engine.buildings[i]
        _strip_survey(b)
        heights._refresh_candidate(b, self.area["params"])


# Nilai otomatis yang ditimpa survei (disimpan di _auto agar survei bisa dibatalkan) dan kunci khusus survei.
SURVEY_LAYER_KEYS = ("levels", "levels_src", "levels_est", "levels_note", "basement", "structure",
                     "elev", "elev_src", "elev_conf", "elev_uncertain")
SURVEY_ONLY_KEYS = ("access", "open_hours", "open_days", "roof", "slf", "verified", "surveyed", "survey", "condition",
                    "elev_method")


def _strip_survey(b: dict) -> None:
    """Kembalikan gedung ke lapisan otomatis (tanpa survei). Nilai None dari _auto berarti kunci tidak ada."""
    auto = b.pop("_auto", None)
    if auto:
        for k, v in auto.items():
            if v is None and k in ("levels_note",):
                b.pop(k, None)
            else:
                b[k] = v
    for k in SURVEY_ONLY_KEYS:
        b.pop(k, None)


PUBLIC_BUILDING_KEYS = ("id", "name", "kind", "levels", "levels_src", "levels_est", "height_m", "min_level", "tier",
                        "hazard", "lat", "lon", "poly", "structure", "access", "open_hours", "open_days", "basement",
                        "surveyed", "condition", "elev_src", "verified_at")


def _relevant_public(b: dict) -> bool:
    if b.get("virtual"):
        return False
    return (int(b.get("levels") or 1) >= 2 or b.get("tier") == "verified" or b.get("surveyed")
            or b.get("kind") not in ("rumah", "tidak_diketahui", "lainnya"))


STAFF_DROP_KEYS = ("_auto", "elev_dem", "elev_road", "elev_dist_m", "elev_uncertain", "geom", "btag")


def _round_geo(d: dict, b: dict) -> dict:
    if d.get("poly"):
        d["poly"] = [[round(x, 6), round(y, 6)] for x, y in d["poly"]]
    d["lat"], d["lon"] = round(b["lat"], 6), round(b["lon"], 6)
    return d


def staff_building(b: dict) -> Optional[dict]:
    """Satu gedung versi petugas/perencana (None = tidak dikirim: asal virtual grid penduduk)."""
    if b.get("virtual"):
        return None
    return _round_geo({k: v for k, v in b.items() if k not in STAFF_DROP_KEYS}, b)


def public_slim_building(b: dict) -> Optional[dict]:
    """Satu gedung versi warga (None = tidak relevan untuk warga, tidak dikirim)."""
    if not _relevant_public(b):
        return None
    return _round_geo({k: v for k, v in public_building(b).items() if k in PUBLIC_BUILDING_KEYS}, b)


def staff_area(area: dict, rev: int = 0, epoch: str = "") -> dict:
    """Area untuk petugas/perencana: SEMUA bangunan (termasuk rumah, untuk peta & survei), tanpa nilai internal dan
    atribut debug elevasi (detail lengkap per gedung: GET /api/buildings/{id}), koordinat 6 desimal (±0,1 m).
    Asal virtual grid penduduk tidak dikirim (tidak punya bangunan)."""
    blds = [d for d in map(staff_building, area.get("buildings", [])) if d is not None]
    return dict(area, meta=dict(area["meta"], view="petugas", rev=rev, epoch=epoch), buildings=blds)


def public_area(area: dict, rev: int = 0, epoch: str = "") -> dict:
    """Area untuk warga (tanpa login). Lihat AreaCtx.refresh_bytes. Data lengkap: login petugas/perencana."""
    blds = [d for d in map(public_slim_building, area.get("buildings", [])) if d is not None]
    nb = [b for b in area.get("buildings", []) if not b.get("virtual")]
    start = next((b for b in nb if b.get("hazard") and float(b.get("residents") or 0) > 0), None)
    meta = dict(area["meta"], view="publik", rev=rev, epoch=epoch, buildings_total=len(nb), buildings_sent=len(blds),
                demo_start=[start["lon"], start["lat"]] if start else None)
    return dict(area, meta=meta, buildings=blds)


def public_building(b: dict) -> dict:
    """Gedung untuk pengguna tanpa login: tanpa nilai internal (_auto) dan tanpa nama petugas/catatan survei.

    Kondisi gedung (lantai, struktur, akses, terverifikasi, tanggal survei) tetap dikirim karena dipakai warga.
    """
    d = {k: v for k, v in b.items() if k not in ("_auto", "survey")}
    if b.get("survey"):
        d["verified_at"] = b["survey"].get("at")
    return d


def public_point(p: dict) -> dict:
    return {k: v for k, v in p.items() if k not in ("created_by", "updated_by")}


def public_closure(c: dict) -> dict:
    return {k: v for k, v in c.items() if k != "created_by"}


def public_alert(a: Optional[dict]) -> Optional[dict]:
    """Peringatan untuk publik: username pembuat dihapus, nama pencabut diganti peran (\"petugas\")."""
    if not a:
        return a
    d = {k: v for k, v in a.items() if k not in ("created_by", "raw")}
    if d.get("ended_by") and d["ended_by"] != "sistem":
        d["ended_by"] = "petugas"
    return d


def _meta_summary(meta: dict) -> dict:
    keep = ("id", "name", "kind", "live", "synthetic", "bbox", "center", "counts", "created", "hazard_method",
            "sources", "expanded", "schema", "timezone", "timezone_src")
    return {k: meta[k] for k in keep if k in meta}


class AppState:
    def __init__(self, areas_dir: Path, db_path: Path, default_area: str = ""):
        self.areas_dir = areas_dir
        self.store = Store(db_path)
        # Grid penduduk regional (sekali unggah, berlaku untuk semua area yang dicakupnya, termasuk peta otomatis).
        self.grids = population.GridStore(Path(db_path).parent / "population")
        self.broker = Broker()
        self.ctx: Dict[str, AreaCtx] = {}
        self.index: Dict[str, dict] = {}
        self.lock = threading.RLock()
        self.build_locks: Dict[str, threading.Lock] = {}
        self.version = 0
        self.bmkg_status = {"enabled": config.BMKG_POLL, "last_check": None, "last_error": None}
        # Alokasi kapasitas berjalan di SATU thread latar belakang (tidak pernah di jalur peringatan/permintaan).
        self.alloc_exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alokasi")
        self.alloc_lock = threading.Lock()
        self.alloc_running: set = set()
        self.alloc_again: set = set()
        self.alloc_idle = threading.Event()
        self.alloc_idle.set()
        self.push = None
        try:
            if webpush.HAVE_CRYPTO:
                self.push = webpush.Vapid.load_or_create(config.DATA_DIR / "vapid_private.pem", config.VAPID_PRIVATE)
        except Exception as exc:  # kunci rusak dsb.
            print(f"Web Push nonaktif: {exc}")
        self._scan()
        if not self.index:
            raise FileNotFoundError("Belum ada data area. Jalankan: python run.py")
        self.default_id = self._pick_default(default_area)
        self.get(self.default_id)

    # ---------- indeks area di disk ----------
    def _scan(self) -> None:
        for p in sorted(self.areas_dir.glob("*/area.json")):
            mp = p.parent / "meta.json"
            try:
                if mp.exists() and mp.stat().st_mtime >= p.stat().st_mtime:
                    meta = json.loads(mp.read_text(encoding="utf-8"))
                else:
                    meta = _meta_summary(json.loads(p.read_text(encoding="utf-8"))["meta"])
                    mp.write_text(json.dumps(meta), encoding="utf-8")
            except Exception as exc:
                print(f"Area {p.parent.name} dilewati: {exc}")
                continue
            self.index[meta["id"]] = meta

    def _pick_default(self, wanted: str) -> str:
        if wanted:
            if wanted not in self.index:
                raise FileNotFoundError(f"Area '{wanted}' tidak ditemukan di {self.areas_dir}")
            return wanted
        prepared = [m for m in self.index.values() if not m.get("live") and not m.get("synthetic")]
        if prepared:
            return prepared[0]["id"]
        synth = [m for m in self.index.values() if m.get("synthetic")]
        return (synth or list(self.index.values()))[0]["id"]

    def save_area(self, area: dict) -> None:
        d = self.areas_dir / area["meta"]["id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "area.json").write_text(json.dumps(area, separators=(",", ":")), encoding="utf-8")
        meta = _meta_summary(area["meta"])
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        with self.lock:
            self.index[meta["id"]] = meta
            self.ctx.pop(meta["id"], None)

    def get(self, aid: Optional[str]) -> AreaCtx:
        aid = aid or self.default_id
        if not ID_RE.match(aid) or aid not in self.index:
            raise HTTPException(404, "Area tidak ditemukan.")
        with self.lock:
            c = self.ctx.get(aid)
            if c is None:
                path = self.areas_dir / aid / "area.json"
                area = area_builder.upgrade_area(json.loads(path.read_text(encoding="utf-8")))
                tz = self.store.get_kv(f"tz:{aid}")  # zona waktu yang ditetapkan perencana (bertahan saat rebuild)
                if tz:
                    area["meta"].update(timezone=tz, timezone_src="perencana")
                if self._reapply_layers(aid, area):  # area dibangun ulang: unggahan lama diterapkan kembali
                    self.save_area(area)
                c = AreaCtx(area, self.grids.covering(area["meta"]["bbox"]))
                for info in self.store.list_building_info(aid):
                    if c.apply_survey(info) is None:
                        c.orphans["surveys"].append(info["building_id"])
                c.orphans["shelters"] = [sid for sid in self.shelter_ids(c) if sid not in c.engine.bindex]
                if c.orphans["surveys"] or c.orphans["shelters"]:
                    # ID gedung tidak ada lagi di data area (mis. area dibangun ulang): TIDAK ditautkan ke gedung lain.
                    print(f"Area {aid}: rujukan ke gedung yang tidak ada lagi {c.orphans}")
                c.refresh_bytes()
                self.ctx[aid] = c
                self._seed(c)
                self._evict()
            c.used = time.time()
        if c.version == 0:
            self.recompute(aid, "load", publish=False)
        return c

    def _reapply_layers(self, aid: str, area: dict) -> bool:
        """Terapkan lapisan unggahan (DB) yang belum ada di area.json, sesuai urutan unggah. True bila ada perubahan.

        Area yang dibangun ulang dari OSM tidak membawa meta.layers_applied, sehingga semua unggahan tinggi gedung,
        peta bahaya, dan hasil Earth Engine diterapkan kembali (ID gedung stabil, jadi tetap tertaut ke gedung sama).
        """
        layers = self.store.list_layers(aid)
        done = list(area["meta"].get("layers_applied") or [])
        todo = [x for x in layers if x["id"] not in done]
        if not todo:
            return False
        eng = Engine(area)
        for lay in todo:
            try:
                data = json.loads(lay["data"])
                if lay["kind"] == "hazard":
                    rings = [[(float(x), float(y)) for x, y in r] for r in data["rings"]]
                    area_builder.apply_hazard_polygons(area, rings, lay["name"] or "peta bahaya resmi (unggahan)")
                    eng = Engine(area)
                else:
                    heights.apply_heights(area, eng, data["features"], source=lay["source"] or "upload",
                                          log=lambda *_: None)
                    if data.get("note"):
                        heights.add_source_note(area, data["note"])
            except Exception as exc:  # lapisan rusak tidak boleh menghalangi area dimuat
                print(f"Area {aid}: lapisan {lay['id']} dilewati: {exc}")
            done.append(lay["id"])
        area["meta"]["layers_applied"] = done
        print(f"Area {aid}: {len(todo)} lapisan unggahan diterapkan ulang.")
        return True

    def _evict(self) -> None:
        if len(self.ctx) <= config.MAX_LOADED_AREAS:
            return
        for aid, c in sorted(self.ctx.items(), key=lambda kv: kv[1].used):
            if aid != self.default_id and len(self.ctx) > config.MAX_LOADED_AREAS:
                del self.ctx[aid]

    def _seed(self, c: AreaCtx) -> None:
        if c.meta.get("synthetic") and self.store.get_kv(f"seeded:{c.id}") is None:
            from .synthetic import seed_points
            for p in seed_points():
                p["sample"] = True
                self.store.add_point(c.id, p, "sistem")
            self.store.set_kv(f"seeded:{c.id}", now_iso())

    def find_covering(self, lat: float, lon: float, margin_m: float = live_area.CORE_MARGIN_M,
                      skip_stale: bool = False) -> Optional[str]:
        """Area yang mencakup posisi; area desa yang disiapkan diutamakan daripada area live."""
        best = None
        for m in self.index.values():
            if skip_stale and m.get("live") and m.get("kind") != "view" \
                    and m.get("schema", 1) < area_builder.AREA_SCHEMA:
                continue  # peta otomatis versi lama (tanpa gedung bertingkat): bangun ulang
            if live_area.covers(m, lat, lon, margin_m if m.get("live") else 0):
                rank = (0 if not m.get("live") else 1, 0 if m.get("kind") == "view" else 1)
                if best is None or rank < best[0]:
                    best = (rank, m["id"])
        return best[1] if best else None

    def locate(self, lat: float, lon: float, build: bool = True) -> dict:
        can_build = build and config.LIVE_AREAS
        aid = self.find_covering(lat, lon, skip_stale=can_build)
        if aid:
            return {"area_id": aid, "built": False}
        if not can_build:
            raise HTTPException(404, "Belum ada peta untuk lokasi ini.")
        lid = live_area.live_id(lat, lon)
        with self.lock:
            lk = self.build_locks.setdefault(lid, threading.Lock())
        with lk:
            aid = self.find_covering(lat, lon, skip_stale=True)  # mungkin sudah dibangun permintaan lain
            if aid:
                return {"area_id": aid, "built": False}
            try:
                area = live_area.build_around(lat, lon, cache_dir=config.CACHE_DIR / "dem")
            except Exception as exc:
                old = self.find_covering(lat, lon)  # versi lama tetap lebih baik daripada tidak ada peta
                if old:
                    return {"area_id": old, "built": False}
                raise HTTPException(502, f"Gagal mengunduh peta di sekitar Anda: {exc}")
            self._maybe_ee(area)
            self.save_area(area)
            return {"area_id": area["meta"]["id"], "built": True}

    @staticmethod
    def _maybe_ee(area: dict) -> None:
        """Opsional: lengkapi tinggi gedung dari Google Open Buildings 2.5D saat peta otomatis dibangun."""
        if not (config.EE_LIVE and config.EE_PROJECT and heights.ee_available()[0]):
            return
        try:
            feats = heights.ee_fetch_heights(area, project=config.EE_PROJECT, year=config.EE_YEAR)
            heights.apply_heights(area, Engine(area), feats, source="google")
            heights.add_source_note(area, f"Tinggi gedung: Google Open Buildings 2.5D Temporal {config.EE_YEAR} (CC BY 4.0)")
        except Exception as exc:  # EE tidak wajib: peta tetap dipakai tanpa data ini
            print(f"  Earth Engine dilewati: {exc}")

    # ---------- peringatan ----------
    def expire_alerts(self) -> bool:
        changed = False
        limit = config.ALERT_MAX_HOURS * 3600
        for a in self.store.active_alerts():
            t = _parse_iso(a.get("issued_at"))
            if t is not None and time.time() - t > limit:
                self.store.end_alert(a["id"], "sistem", f"Berakhir otomatis setelah {config.ALERT_MAX_HOURS:g} jam")
                changed = True
        return changed

    @staticmethod
    def alert_reaches(a: dict, lat: float, lon: float) -> bool:
        r = a.get("radius_km")
        if r is None or a.get("lat") is None:
            return True
        return haversine_m(a["lat"], a["lon"], lat, lon) <= r * 1000

    def alert_for(self, c: AreaCtx) -> Optional[dict]:
        lon, lat = c.meta["center"]
        for a in self.store.active_alerts():
            if self.alert_reaches(a, lat, lon):
                return a
        return None

    @staticmethod
    def wave_for(c: AreaCtx, alert: Optional[dict]) -> Optional[float]:
        """Tinggi tsunami untuk menghitung lantai aman; None = skenario rencana area."""
        if not alert:
            return None
        return tsunami.wave_height_for(alert.get("level"), alert.get("wave_m"), c.engine.design_wave_m)

    # ---------- titik bantuan ----------
    def points_for(self, c: AreaCtx) -> List[dict]:
        return [dict(p, verified=True) for p in self.store.list_points(c.id)]

    def closures(self, c: AreaCtx) -> set:
        return {int(x["edge"]) for x in self.store.list_closures(c.id)}

    def shelter_ids(self, c: AreaCtx) -> List[str]:
        return [s["building_id"] for s in self.store.list_shelters(c.id)]

    def occupancy(self, c: AreaCtx) -> Dict[str, int]:
        """Okupansi gedung yang dilaporkan petugas (orang sudah di dalam): mengurangi kapasitas tersedia."""
        raw = self.store.get_kv(f"occ:{c.id}")
        try:
            return {k: int(v) for k, v in (json.loads(raw) if raw else {}).items() if int(v) > 0}
        except (ValueError, TypeError):
            return {}

    # ---------- alokasi kapasitas di latar belakang ----------
    def schedule_alloc(self, aid: str) -> None:
        with self.alloc_lock:
            if aid in self.alloc_running:
                self.alloc_again.add(aid)
                return
            self.alloc_running.add(aid)
            self.alloc_idle.clear()
        self.alloc_exec.submit(self._alloc_job, aid)

    def _alloc_job(self, aid: str) -> None:
        try:
            while True:
                c = self.ctx.get(aid)
                if c is not None:
                    eng, reqs = c.engine, list(c.alloc_requests)
                    for r in reqs:
                        try:
                            eng.allocate(r["mode"], r["dests"], closures=r["closures"], high=r["high"], H=r["H"],
                                         budget_s=r["budget_s"], time_limit_s=config.ALLOC_TIME_LIMIT_S)
                        except Exception as exc:  # alokasi gagal TIDAK boleh mengganggu peringatan/rute
                            print(f"Alokasi {aid}/{r['mode']} gagal: {exc}")
                            eng._alloc[eng.alloc_key(r["mode"], r["dests"], r["closures"], r["H"], r["budget_s"])] = {
                                "status": "gagal", "result": {"errors": [str(exc)[:200]], "meta": {}},
                                "pen": [0.0] * len(r["dests"]), "overrides": {}, "table_errors": [str(exc)[:200]]}
                    if reqs and self.ctx.get(aid) is c:
                        self.recompute(aid, "kapasitas", schedule=False)
                with self.alloc_lock:
                    if aid in self.alloc_again:
                        self.alloc_again.discard(aid)
                        continue
                    self.alloc_running.discard(aid)
                    if not self.alloc_running:
                        self.alloc_idle.set()
                    return
        except Exception as exc:  # pragma: no cover
            print(f"Pekerja alokasi {aid}: {exc}")
            with self.alloc_lock:
                self.alloc_running.discard(aid)
                if not self.alloc_running:
                    self.alloc_idle.set()

    def wait_allocations(self, timeout: float = 120.0) -> bool:
        """Untuk uji/benchmark: tunggu sampai semua alokasi latar belakang selesai."""
        return self.alloc_idle.wait(timeout)

    @staticmethod
    def at_for(c: AreaCtx, alert: Optional[dict]) -> tsunami.AlertTime:
        """Waktu untuk akses gedung: jam terbit peringatan (atau sekarang bila tanpa peringatan) dalam zona waktu area.

        Gedung harus terbuka sepanjang perkiraan waktu sampai gelombang tiba (eta peringatan, atau default area).
        """
        ts = _parse_iso(alert.get("issued_at")) if alert else None
        eta = (alert or {}).get("eta_min") or c.area["params"].get("default_eta_min", 20.0)
        return tsunami.alert_time(ts if ts is not None else time.time(), c.tz, float(eta) * 60.0)

    def refresh_access(self, c: AreaCtx) -> None:
        """Tanpa peringatan, tabel dihitung dengan jam sekarang: hitung ulang saat jam buka/tutup suatu gedung lewat."""
        if c.valid_until is not None and time.time() >= c.valid_until:
            self.recompute(c.id, "jam")

    def recompute(self, aid: str, reason: str, publish: bool = True, schedule: bool = True) -> None:
        """Tabel HP. Alokasi kapasitas TIDAK dihitung di sini: hanya hasil yang sudah ada untuk skenario yang persis
        sama dipakai; selain itu status "pending" dan perhitungan dijadwalkan di latar belakang."""
        c = self.ctx.get(aid) or self.get(aid)
        alert = self.alert_for(c)
        at = self.at_for(c, alert)
        with c.lock:
            lt = c.engine.live_tables(closures=self.closures(c), shelter_ids=self.shelter_ids(c),
                                      points=self.points_for(c) + c.facilities, alert_active=alert is not None,
                                      wave_m=self.wave_for(c, alert), at=at,
                                      eta_min=(alert or {}).get("eta_min"), alloc="cached",
                                      occupancy=self.occupancy(c))
            c.alloc_requests = lt.pop("_alloc_requests", [])
            c.valid_until = None if alert else lt.get("valid_until")
            with self.lock:
                self.version += 1
                c.version = self.version
            payload = {"version": c.version, "generated_at": now_iso(), "alert_active": alert is not None,
                       "area_id": c.id, **lt}
            c.tables_bytes = json.dumps(payload, separators=(",", ":")).encode()
        if c.alloc_requests and schedule:
            self.schedule_alloc(c.id)
        elif c.alloc_requests and not schedule:
            with self.alloc_lock:
                self.alloc_again.add(c.id)  # skenario berubah selagi menghitung: hitung lagi
        if publish or reason == "kapasitas":
            self.broker.publish("state", {"version": c.version, "area": c.id, "reason": reason})

    def recompute_all(self, reason: str) -> None:
        for aid in list(self.ctx):
            self.recompute(aid, reason, publish=False)
        self.broker.publish("state", {"version": self.version, "area": None, "reason": reason})

    def state(self, aid: Optional[str], internal: bool = False) -> dict:
        """Keadaan area. internal=False (tanpa login): tanpa username petugas."""
        c = self.get(aid)
        self.refresh_access(c)
        active = self.alert_for(c)
        last = None
        if not active:
            lon, lat = c.meta["center"]
            for a in self.store.latest_alerts(5):
                if a["status"] != "aktif" and self.alert_reaches(a, lat, lon):
                    last = a
                    break
        shelters = []
        wave = self.wave_for(c, active)
        at = self.at_for(c, active)
        for sid in self.shelter_ids(c):
            i = c.engine.bindex.get(sid)
            if i is not None:
                b = c.engine.buildings[i]
                a = c.engine.assess(b, wave, at)
                ok, why = tsunami.registered_status(b, a)
                shelters.append({"id": sid, "name": b["name"] or f"Gedung {b['levels']} lantai", "lat": b["lat"],
                                 "lon": b["lon"], "levels": b["levels"], "capacity": a["capacity"],
                                 "min_floor": a["min_floor"], "usable": ok,
                                 "tier": a["tier"], "tier_label": a["tier_label"], "caveats": a["caveats"],
                                 "reason": why})
        points, closures = self.store.list_points(c.id), self.store.list_closures(c.id)
        if not internal:
            active, last = public_alert(active), public_alert(last)
            points = [public_point(x) for x in points]
            closures = [public_closure(x) for x in closures]
        out = {"version": c.version, "area_id": c.id, "server_time": now_iso(), "alert": active, "last_alert": last,
               "points": points, "closures": closures,
               "shelters": shelters, "scenario": {"wave_m": c.engine.wave(wave), "design": wave is None,
                                                  "level": active.get("level") if active else None,
                                                  "time": tsunami.local_time_label(at)},
               "timezone": c.tz}
        if internal:
            out["orphans"] = c.orphans
        return out

    # ---------- Web Push ----------
    def push_send(self, sub_row: dict, payload: dict) -> int:
        """Kirim ke satu pelanggan. Tidak pernah melempar exception: -1 = gagal (jaringan, timeout, data rusak)."""
        if not self.push:
            return 0
        try:
            sub = json.loads(sub_row["sub"]) if isinstance(sub_row.get("sub"), str) else sub_row
            code, _ = webpush.send(sub, payload, self.push, contact=config.PUSH_CONTACT,
                                   urgency="high" if payload.get("type") in ("alert", "sim") else "normal",
                                   timeout=config.PUSH_TIMEOUT_S)
            if code in (404, 410):  # langganan sudah tidak berlaku
                self.store.delete_sub(sub["endpoint"])
            return code
        except Exception:
            return -1

    def _push_many(self, payload: dict, center=None, radius_km: Optional[float] = None) -> dict:
        """Kirim ke semua pelanggan (dalam radius) secara paralel; yang terdekat dengan pusat peringatan dulu."""
        targets = []
        for row in self.store.list_subs():
            d = 0.0
            if center is not None and radius_km is not None:
                if row.get("lat") is None:
                    continue
                d = haversine_m(center[0], center[1], row["lat"], row["lon"])
                if d > radius_km * 1000:
                    continue
            targets.append((d, row))
        targets.sort(key=lambda t: t[0])
        stats = {"total": len(targets), "ok": 0, "gone": 0, "failed": 0}
        if not targets:
            return stats
        workers = max(1, min(config.PUSH_WORKERS, len(targets)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="push") as ex:
            for code in ex.map(lambda t: self.push_send(t[1], payload), targets):
                key = "ok" if code in (200, 201, 202) else "gone" if code in (404, 410) else "failed"
                stats[key] += 1
        if stats["failed"]:
            print(f"Push {payload.get('type')}: {stats}")
        return stats

    def push_broadcast(self, payload: dict, center=None, radius_km: Optional[float] = None,
                       wait: bool = False) -> Optional[dict]:
        """Siarkan notifikasi. Bawaan: di latar belakang (request tidak menunggu). wait=True: tunggu & beri statistik."""
        if not self.push:
            return None
        if wait:
            return self._push_many(payload, center, radius_km)
        threading.Thread(target=self._push_many, args=(payload, center, radius_km), daemon=True).start()
        return None


STATE: Optional[AppState] = None


def get_state() -> AppState:
    if STATE is None:
        raise HTTPException(503, "Server belum siap")
    return STATE


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def current_user(authorization: Optional[str] = Header(default=None)) -> Optional[dict]:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return auth.check(authorization[7:].strip())


def require(*roles: str):
    def dep(user: Optional[dict] = Depends(current_user)) -> dict:
        if user is None:
            raise HTTPException(401, "Silakan masuk terlebih dahulu.")
        if roles and user["role"] not in roles and user["role"] != "admin":
            raise HTTPException(403, "Akun Anda tidak memiliki akses untuk tindakan ini.")
        return user
    return dep


# ---------------------------------------------------------------------------
# Skema permintaan
# ---------------------------------------------------------------------------
class LoginIn(BaseModel):
    username: str
    password: str


class LocateIn(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    build: bool = True


class AnalyzeIn(BaseModel):
    bbox: List[float] = Field(..., min_length=4, max_length=4)  # [barat, selatan, timur, utara]
    name: str = Field("", max_length=80)


class RouteIn(BaseModel):
    lat: float
    lon: float
    mode: str = Field("aman", pattern="^(aman|posko|gedung)$")
    group: str = Field("umum", pattern="^(umum|rentan)$")
    alternatives: int = Field(3, ge=1, le=8)
    area_id: Optional[str] = None
    device_key: Optional[str] = Field(None, max_length=64)  # sama dengan HP: memilih opsi asal yang dipecah


class OccupancyIn(BaseModel):
    occupied: int = Field(..., ge=0, le=1000000)
    area_id: Optional[str] = None


class PointIn(BaseModel):
    kind: str
    name: str = Field(..., min_length=2, max_length=80)
    lat: float
    lon: float
    status: str = "kosong"
    services: List[str] = []
    capacity: Optional[int] = Field(None, ge=0, le=100000)
    note: str = Field("", max_length=200)
    area_id: Optional[str] = None


class PointPatch(BaseModel):
    kind: Optional[str] = None
    name: Optional[str] = Field(None, min_length=2, max_length=80)
    lat: Optional[float] = None
    lon: Optional[float] = None
    status: Optional[str] = None
    services: Optional[List[str]] = None
    capacity: Optional[int] = Field(None, ge=0, le=100000)
    note: Optional[str] = Field(None, max_length=200)


class ClosureIn(BaseModel):
    lat: float
    lon: float
    note: str = Field("", max_length=200)
    area_id: Optional[str] = None


class SimulateIn(BaseModel):
    magnitude: float = Field(7.2, ge=4.0, le=9.9)
    depth_km: float = Field(20.0, ge=0, le=700)
    distance_km: float = Field(150.0, ge=5, le=1500)
    tsunami: bool = True
    eta_min: Optional[float] = Field(None, ge=1, le=240)
    area_id: Optional[str] = None
    scope: str = Field("area", pattern="^(area|all)$")
    level: str = Field("awas", pattern="^(waspada|siaga|awas)$")
    wave_m: Optional[float] = Field(None, ge=0.1, le=40)


class EndAlertIn(BaseModel):
    note: str = Field(..., min_length=3, max_length=200)


class PlanIn(BaseModel):
    # Waktu skenario untuk akses gedung: "terburuk" (bawaan: gedung berjam buka terbatas dianggap tutup),
    # "peringatan" (jam peringatan aktif), "sekarang", atau "HH:MM" waktu setempat area (+ scenario_day, Senin = 0).
    scenario_time: str = Field("terburuk", pattern=r"^(terburuk|peringatan|sekarang|([01]\d|2[0-3]):[0-5]\d)$")
    scenario_day: Optional[int] = Field(None, ge=0, le=6)
    eta_min: float = Field(20, ge=1, le=240)
    delay_min: float = Field(5, ge=0, le=120)
    margin_min: float = Field(2, ge=0, le=60)
    optimize_k: int = Field(0, ge=0, le=15)
    use_registered: bool = True
    area_id: Optional[str] = None
    design_height_m: Optional[float] = Field(None, ge=0.5, le=40)


class PointInfoIn(BaseModel):
    lat: float
    lon: float
    eta_min: float = Field(20, ge=1, le=240)
    llm: bool = False
    area_id: Optional[str] = None


class SurveyIn(BaseModel):
    area_id: Optional[str] = None
    levels: Optional[int] = Field(None, ge=1, le=200)
    basement: int = Field(0, ge=0, le=10)
    structure: str = Field("tidak_tahu", pattern="^(beton|baja|bata|ringan|tidak_tahu)$")
    # Tidak diisi = akses belum diketahui ("Perlu Verifikasi"). Dulu bawaannya "24jam" (dianggap terbuka): diubah.
    access: Optional[str] = Field(None, pattern="^(24jam|jam_kerja|tidak)$")
    # Petugas menyatakan jumlah lantai DIHITUNG langsung di lokasi (wajib bila `levels` diisi). Audit D-7.
    levels_confirmed: bool = False
    # Kondisi bangunan hasil pengamatan; hanya "baik" yang bisa terverifikasi. Tidak diisi = belum dicatat.
    condition: Optional[str] = Field(None, pattern="^(baik|rusak_ringan|rusak_berat|dalam_pembangunan|terbengkalai)$")
    # Elevasi tanah terukur (m di atas permukaan laut) + metode; hanya metode lapangan yang diakui (tsunami.ELEV_METHODS).
    ground_elev_m: Optional[float] = Field(None, ge=-20, le=3000)
    ground_elev_method: Optional[str] = Field(None, pattern="^(gnss_geodetik|benchmark|survei_topografi)$")
    # Wajib untuk "jam_kerja": jam buka sebenarnya, mis. "07:00-16:00" atau "04:30-06:00, 11:30-20:00".
    open_hours: Optional[str] = Field(None, max_length=80)
    open_days: Optional[str] = Field(None, pattern="^[0-6]{1,7}$")  # Senin = 0; kosong = setiap hari
    roof: bool = False
    slf: bool = False
    note: str = Field("", max_length=200)


class GeoUploadIn(BaseModel):
    """Sumber data TIDAK bisa dipilih pengunggah: unggahan = "upload"; hanya fitur berproperti
    sempat_source="google_open_buildings" (ditulis tools/ee_export_heights.js) yang dicatat sebagai "google"."""
    geojson: dict
    name: str = Field("", max_length=120)


class TimezoneIn(BaseModel):
    timezone: str = Field(..., pattern=r"^(Asia/Jakarta|Asia/Pontianak|Asia/Makassar|Asia/Jayapura|[A-Za-z_]+/[A-Za-z_/]+|UTC[+-]\d{1,2})$")


class EEIn(BaseModel):
    year: int = Field(2023, ge=2016, le=2023)
    add_missing: bool = True


class SubIn(BaseModel):
    subscription: dict
    lat: Optional[float] = None
    lon: Optional[float] = None


class EndpointIn(BaseModel):
    endpoint: str = Field(..., max_length=1000)


class SelfSimIn(BaseModel):
    lat: float
    lon: float
    delay_s: int = Field(10, ge=0, le=120)
    endpoint: Optional[str] = Field(None, max_length=1000)


# ---------------------------------------------------------------------------
# Peringatan & BMKG
# ---------------------------------------------------------------------------
def _alert_payload(a: dict, kind: str = "alert") -> dict:
    sim = a.get("source") == "SIMULASI"
    title = (a.get("headline") or "PERINGATAN DINI TSUNAMI") + (" — SIMULASI" if sim else "")
    if a.get("level") == "waspada":
        act = "Jauhi pantai dan tepi sungai. Tidak perlu mengungsi ke tempat tinggi."
    else:
        act = "Segera menjauh dari pantai dan sungai, naik ke tempat tinggi. Ketuk untuk melihat arah."
    body = ("[SIMULASI] " if sim else "") + act
    keep = ("id", "source", "event", "magnitude", "depth_km", "lat", "lon", "region", "tsunami", "issued_at",
            "eta_min", "headline", "instruction", "radius_km", "self", "level", "wave_m")
    return {"type": kind, "title": title, "body": body, "tag": a.get("id"), "url": f"/?alert={a.get('id')}",
            "alert": {k: a.get(k) for k in keep if k in a}}


def create_alert(st: AppState, a: dict) -> dict:
    """Peringatan dikirim LEBIH DULU (SSE + Web Push di latar belakang); tabel rute dihitung sesudahnya dan alokasi
    kapasitas berjalan di thread terpisah. Kegagalan perhitungan apa pun tidak boleh menahan peringatan."""
    a = st.store.add_alert(a)
    st.broker.publish("alert", public_alert(a))  # SSE terbuka untuk semua klien
    center = (a["lat"], a["lon"]) if a.get("radius_km") is not None else None
    try:
        st.push_broadcast(_alert_payload(a), center=center, radius_km=a.get("radius_km"))
    except Exception as exc:  # pragma: no cover - push tidak boleh menggagalkan peringatan
        print(f"Push peringatan gagal dijadwalkan: {exc}")
    try:
        st.recompute_all("alert")  # tabel dasar (Dijkstra, tanpa optimasi); alokasi dijadwalkan di latar belakang
    except Exception as exc:  # pragma: no cover
        print(f"Tabel rute gagal dihitung ulang setelah peringatan: {exc}")
    return a


def handle_bmkg_event(st: AppState, ev: Optional[dict], doc: Optional[dict] = None) -> None:
    """Kejadian baru dari BMKG: simpan sebagai gempa terbaru, kirim info, dan buat peringatan bila berpotensi tsunami."""
    if not ev or not ev.get("key") or ev["key"] == st.store.get_kv("bmkg_last_key"):
        return
    st.store.set_kv("bmkg_last_key", ev["key"])
    st.store.set_kv("bmkg_last_quake", json.dumps(ev))
    st.broker.publish("quake", ev)
    t = _parse_iso(ev.get("issued_at"))
    fresh = t is None or time.time() - t < 30 * 60  # jangan kirim ulang gempa lama saat server baru menyala
    if not fresh or ev.get("lat") is None:
        return
    if ev.get("tsunami"):
        eta = 20.0
        txt = guidance.alert_text(tsunami=True, magnitude=ev["magnitude"], region=ev["region"], eta_min=eta,
                                  simulated=False)
        a = create_alert(st, {"source": "BMKG", "event": "Tsunami", "magnitude": ev["magnitude"],
                              "depth_km": ev["depth_km"], "lat": ev["lat"], "lon": ev["lon"], "region": ev["region"],
                              "tsunami": 1, "issued_at": ev.get("issued_at") or now_iso(), "eta_min": eta,
                              "headline": txt["headline"], "instruction": txt["instruction"],
                              "radius_km": config.BMKG_RADIUS_KM, "created_by": "bmkg",
                              "raw": json.dumps(doc)[:5000] if doc else None})
        st.store.audit("bmkg", "alert_from_bmkg", {"id": a["id"], "key": ev["key"]})
    else:
        mag = f"M{ev['magnitude']:.1f}".replace(".", ",") if ev.get("magnitude") is not None else "Gempa"
        st.push_broadcast({"type": "quake", "title": f"Info gempa {mag} — Sumber: BMKG",
                           "body": f"{ev.get('region', '')} · {ev.get('local_time', '')} · {ev.get('potensi', '')}",
                           "tag": f"quake-{ev['key']}", "url": "/"},
                          center=(ev["lat"], ev["lon"]), radius_km=config.BMKG_INFO_RADIUS_KM)


async def _bmkg_loop(st: AppState) -> None:
    while True:
        try:
            doc = await asyncio.to_thread(bmkg.fetch, config.BMKG_URL)
            ev = bmkg.parse_autogempa(doc)
            st.bmkg_status.update({"last_check": now_iso(), "last_error": None})
            await asyncio.to_thread(handle_bmkg_event, st, ev, doc)
        except Exception as exc:  # jaringan putus, format berubah, dsb.
            st.bmkg_status.update({"last_check": now_iso(), "last_error": str(exc)[:200]})
        try:
            if st.expire_alerts():
                st.recompute_all("alert_end")
                st.broker.publish("alert", {"status": "expired"})
        except Exception:
            pass
        await asyncio.sleep(config.BMKG_INTERVAL_S)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global STATE
    try:
        auth.ensure_accounts()  # di server publik: tanpa akun demo (lihat auth.py)
    except Exception as exc:  # users.json rusak dsb.: server tetap jalan, login ditolak
        print(f"Akun petugas/perencana tidak bisa disiapkan: {exc}")
    if STATE is None:
        STATE = AppState(config.AREAS_DIR, config.DB_PATH, config.AREA_ID)
    STATE.broker.bind(asyncio.get_running_loop())
    task = asyncio.create_task(_bmkg_loop(STATE)) if config.BMKG_POLL else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="SEMPAT API", version=__version__, lifespan=lifespan)


class _GZip(GZipMiddleware):
    """Kompresi untuk semua respons kecuali aliran SSE (agar event tidak tertahan buffer)."""

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") == "/api/stream":
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


app.add_middleware(_GZip, minimum_size=1000)


def _json(data, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})


# ---- info & data ----
@app.get("/api/meta")
def api_meta(area: Optional[str] = None, st: AppState = Depends(get_state)):
    c = st.get(area)
    return _json({"version": __version__, "area": c.meta, "params": c.area["params"], "server_time": now_iso(),
                  "bmkg": st.bmkg_status, "demo": bool(c.meta.get("synthetic")),
                  "default_area": st.default_id, "live_areas": config.LIVE_AREAS,
                  "point_kinds": POINT_KINDS, "services": SERVICES, "statuses": POINT_STATUSES,
                  "llm": bool(config.LLM_API_KEY and config.LLM_MODEL), "public_url": config.PUBLIC_URL,
                  "demo_accounts": auth.demo_accounts_allowed(), "timezone": c.tz,
                  "push_key": st.push.public_b64 if st.push else None})


@app.get("/api/areas")
def api_areas(st: AppState = Depends(get_state)):
    items = sorted(st.index.values(), key=lambda m: (m.get("live", False), -(_parse_iso(m.get("created")) or 0)))
    return _json({"default": st.default_id, "areas": items})


@app.post("/api/locate")
def api_locate(body: LocateIn, st: AppState = Depends(get_state)):
    r = st.locate(body.lat, body.lon, build=body.build)
    c = st.get(r["area_id"])
    return _json({**r, "meta": _meta_summary(c.meta)})


@app.post("/api/analyze")
def api_analyze(body: AnalyzeIn, user: dict = Depends(require("perencana")), st: AppState = Depends(get_state)):
    w, s, e, n = body.bbox
    if not (-90 <= s < n <= 90 and -180 <= w < e <= 180):
        raise HTTPException(400, "Batas area tidak valid.")
    lat_c = (s + n) / 2
    km2 = (n - s) * 111.32 * (e - w) * 111.32 * max(0.2, math.cos(math.radians(lat_c)))
    if km2 > 16:
        raise HTTPException(400, f"Area terlalu luas ({km2:.0f} km²). Perbesar peta (zoom in) hingga ≤ 16 km².")
    if km2 < 0.05:
        raise HTTPException(400, "Area terlalu kecil.")
    aid = "view-" + hashlib.sha1(f"{w:.4f},{s:.4f},{e:.4f},{n:.4f}".encode()).hexdigest()[:10]
    if aid in st.index and st.index[aid].get("schema", 1) >= area_builder.AREA_SCHEMA:
        return _json({"area_id": aid, "built": False, "meta": st.index[aid]})
    place, state = (None, None) if live_area._test_source() else osm_fetch.reverse_place(lat_c, (w + e) / 2)
    name = body.name.strip() or place or f"Area {lat_c:.3f}, {(w + e) / 2:.3f}"
    src = live_area._test_source() or live_area.internet_source(config.CACHE_DIR / "dem", print)
    try:
        area = live_area.build_bbox((s, w, n, e), area_id=aid, name=name, buildings=True, source=src, kind="view")
    except Exception as exc:
        raise HTTPException(502, f"Gagal mengunduh data peta: {exc}")
    live_area.set_timezone_from_province(area, state)
    st.save_area(area)
    st.store.audit(user["username"], "analyze_area", {"id": aid, "bbox": body.bbox})
    return _json({"area_id": aid, "built": True, "meta": st.index[aid]})


@app.get("/api/area")
def api_area(area: Optional[str] = None, user: Optional[dict] = Depends(current_user),
             st: AppState = Depends(get_state)):
    """Data area. Tanpa login: versi publik (tanpa _auto, nama petugas, catatan survei) disaring di server."""
    c = st.get(area)
    return Response(c.staff_bytes if user else c.public_bytes, media_type="application/json",
                    headers={"Cache-Control": "no-cache", "Vary": "Authorization"})


@app.get("/api/area/patch")
def api_area_patch(since: int = Query(..., ge=0), epoch: str = Query(..., max_length=16), area: Optional[str] = None,
                   user: Optional[dict] = Depends(current_user), st: AppState = Depends(get_state)):
    """Tambalan area sejak revisi `since`: hanya gedung yang berubah (versi sesuai peran). 409 = klien harus memuat
    ulang /api/area (perubahan menyeluruh, atau riwayat revisi sudah tidak lengkap)."""
    c = st.get(area)
    with c.lock:
        ids = c.changed_since(since) if epoch == c.epoch else None
        if ids is None:
            raise HTTPException(409, "Perubahan menyeluruh: muat ulang area.")
        conv = staff_building if user else public_slim_building
        out, removed = [], []
        for bid in sorted(ids):
            i = c.engine.bindex.get(bid)
            d = conv(c.engine.buildings[i]) if i is not None else None
            (out.append(d) if d is not None else removed.append(bid))
        return _json({"area": c.id, "rev": c.rev, "epoch": c.epoch, "since": since, "buildings": out,
                      "removed": removed})


@app.get("/api/tables")
def api_tables(area: Optional[str] = None, st: AppState = Depends(get_state)):
    return Response(st.get(area).tables_bytes, media_type="application/json", headers={"Cache-Control": "no-store"})


@app.get("/api/state")
def api_state(area: Optional[str] = None, user: Optional[dict] = Depends(current_user),
              st: AppState = Depends(get_state)):
    if st.expire_alerts():
        st.recompute_all("alert_end")
    return _json(st.state(area, internal=user is not None))


@app.get("/api/quake")
def api_quake(st: AppState = Depends(get_state)):
    q = st.store.get_kv("bmkg_last_quake")
    return _json({"quake": json.loads(q) if q else None, "bmkg": st.bmkg_status})


@app.get("/api/stream")
async def api_stream(request: Request, st: AppState = Depends(get_state)):
    q = st.broker.subscribe()

    async def gen():
        try:
            yield f"event: hello\ndata: {json.dumps({'version': st.version})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            st.broker.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- rute ----
def _area_for(st: AppState, area_id: Optional[str], lat: float, lon: float) -> AreaCtx:
    if area_id:
        return st.get(area_id)
    aid = st.find_covering(lat, lon, margin_m=0)
    if not aid:
        raise HTTPException(404, "Belum ada peta untuk lokasi ini.")
    return st.get(aid)


@app.post("/api/route")
def api_route(body: RouteIn, st: AppState = Depends(get_state)):
    c = _area_for(st, body.area_id, body.lat, body.lon)
    alert_on = st.alert_for(c) is not None
    if body.mode == "posko" and alert_on:
        s = c.engine.snap(body.lat, body.lon)
        if s is not None:
            node = s["u"] if s["t"] < 0.5 else s["v"]
            if c.engine.hazard[node]:
                return _json({"ok": False, "code": "in_hazard",
                              "reason": "Anda masih di zona bahaya. Menuju tempat aman lebih dulu."})
    alert = st.alert_for(c)
    r = c.engine.route(body.lat, body.lon, mode=body.mode, group=body.group, closures=st.closures(c),
                       shelter_ids=st.shelter_ids(c), points=st.points_for(c) + c.facilities, alert_active=alert_on,
                       alternatives=body.alternatives, wave_m=st.wave_for(c, alert), at=st.at_for(c, alert),
                       eta_min=(alert or {}).get("eta_min"), alloc="cached", device_key=body.device_key,
                       occupancy=st.occupancy(c))
    r["area_id"] = c.id
    return _json(r)


# ---- auth ----
@app.post("/api/auth/login")
def api_login(body: LoginIn):
    user = auth.verify(body.username.strip(), body.password)
    if not user:
        time.sleep(0.5)
        raise HTTPException(401, "Nama pengguna atau kata sandi salah.")
    return _json({"token": auth.issue(user), "user": user})


@app.get("/api/auth/me")
def api_me(user: dict = Depends(require())):
    return _json({"user": user})


# ---- titik bantuan ----
def _check_point_fields(c: AreaCtx, kind: Optional[str], status: Optional[str], services: Optional[List[str]],
                        lat: Optional[float], lon: Optional[float]) -> None:
    if kind is not None and kind not in POINT_KINDS:
        raise HTTPException(400, "Jenis titik tidak dikenal.")
    if status is not None and status not in POINT_STATUSES:
        raise HTTPException(400, "Status tidak dikenal.")
    if services is not None and any(s not in SERVICES for s in services):
        raise HTTPException(400, "Layanan tidak dikenal.")
    if lat is not None and lon is not None:
        w, s, e, n = c.meta["bbox"]
        pad = 0.01
        if not (s - pad <= lat <= n + pad and w - pad <= lon <= e + pad):
            raise HTTPException(400, "Lokasi berada di luar wilayah peta ini. Pindah wilayah dulu.")


@app.get("/api/points")
def api_points(area: Optional[str] = None, user: Optional[dict] = Depends(current_user),
               st: AppState = Depends(get_state)):
    pts = st.store.list_points(st.get(area).id)
    return _json(pts if user else [public_point(p) for p in pts])


@app.post("/api/points")
def api_add_point(body: PointIn, user: dict = Depends(require("petugas", "perencana")),
                  st: AppState = Depends(get_state)):
    c = _area_for(st, body.area_id, body.lat, body.lon)
    _check_point_fields(c, body.kind, body.status, body.services, body.lat, body.lon)
    p = st.store.add_point(c.id, body.model_dump(exclude={"area_id"}), user["username"])
    st.store.audit(user["username"], "add_point", {"id": p["id"], "name": p["name"], "area": c.id})
    st.recompute(c.id, "points")
    return _json(p, 201)


@app.patch("/api/points/{pid}")
def api_patch_point(pid: str, body: PointPatch, user: dict = Depends(require("petugas", "perencana")),
                    st: AppState = Depends(get_state)):
    cur = st.store.get_point(pid)
    if cur is None:
        raise HTTPException(404, "Titik tidak ditemukan.")
    c = st.get(cur["area_id"])
    fields = body.model_dump(exclude_none=True)
    _check_point_fields(c, fields.get("kind"), fields.get("status"), fields.get("services"),
                        fields.get("lat"), fields.get("lon"))
    p = st.store.update_point(pid, fields, user["username"])
    st.store.audit(user["username"], "update_point", {"id": pid, **fields})
    st.recompute(c.id, "points")
    return _json(p)


@app.delete("/api/points/{pid}")
def api_delete_point(pid: str, user: dict = Depends(require("petugas", "perencana")),
                     st: AppState = Depends(get_state)):
    cur = st.store.get_point(pid)
    st.store.delete_point(pid)
    st.store.audit(user["username"], "delete_point", {"id": pid})
    if cur:
        st.recompute(cur["area_id"], "points")
    return _json({"ok": True})


# ---- jalan putus ----
@app.post("/api/closures")
def api_add_closure(body: ClosureIn, user: dict = Depends(require("petugas", "perencana")),
                    st: AppState = Depends(get_state)):
    c = _area_for(st, body.area_id, body.lat, body.lon)
    s = c.engine.snap(body.lat, body.lon)
    if s is None or s["perp_m"] > 60:
        raise HTTPException(400, "Ketuk lebih dekat ke ruas jalan yang putus.")
    cl = st.store.add_closure(c.id, s["edge"], s["lat"], s["lon"], body.note, user["username"])
    st.store.audit(user["username"], "add_closure", {"edge": s["edge"], "note": body.note, "area": c.id})
    st.recompute(c.id, "closures")
    return _json(cl, 201)


@app.delete("/api/closures/{cid}")
def api_delete_closure(cid: str, user: dict = Depends(require("petugas", "perencana")),
                       st: AppState = Depends(get_state)):
    rows = st.store._q("SELECT area_id FROM closures WHERE id=?", (cid,))
    st.store.delete_closure(cid)
    st.store.audit(user["username"], "delete_closure", {"id": cid})
    if rows:
        st.recompute(rows[0]["area_id"], "closures")
    return _json({"ok": True})


@app.get("/api/edges/{edge}")
def api_edge(edge: int, area: Optional[str] = None, st: AppState = Depends(get_state)):
    e = st.get(area).engine
    if not 0 <= edge < e.m:
        raise HTTPException(404, "Ruas tidak ditemukan.")
    u, v = e.eu[edge], e.ev[edge]
    return _json({"edge": edge, "coords": [[e.lon[u], e.lat[u]], [e.lon[v], e.lat[v]]], "length_m": e.elen[edge]})


# ---- peringatan ----
@app.get("/api/alerts")
def api_alerts(user: Optional[dict] = Depends(current_user), st: AppState = Depends(get_state)):
    al = st.store.list_alerts()
    return _json(al if user else [public_alert(a) for a in al])


@app.post("/api/alerts/simulate")
def api_simulate(body: SimulateIn, user: dict = Depends(require("petugas", "perencana")),
                 st: AppState = Depends(get_state)):
    c = st.get(body.area_id)
    if st.alert_for(c):
        raise HTTPException(409, "Masih ada peringatan aktif. Cabut dulu sebelum membuat simulasi baru.")
    lon0, lat0 = c.meta["center"]
    lat = lat0 - body.distance_km / 111.2  # pusat gempa simulasi di laut, arah selatan area
    eta = body.eta_min if body.eta_min is not None else float(c.area["params"].get("default_eta_min", 20))
    region = f"{body.distance_km:.0f} km arah selatan {c.meta['name']}"
    level = body.level if body.tsunami else None
    txt = guidance.alert_text(tsunami=body.tsunami, magnitude=body.magnitude, region=region, eta_min=eta,
                              simulated=True, level=level)
    a = create_alert(st, {"source": "SIMULASI", "event": "Tsunami" if body.tsunami else "Gempa",
                          "magnitude": body.magnitude, "depth_km": body.depth_km, "lat": lat, "lon": lon0,
                          "region": region, "tsunami": 1 if body.tsunami else 0,
                          "eta_min": eta if body.tsunami else None, "headline": txt["headline"],
                          "instruction": txt["instruction"], "created_by": user["username"],
                          "radius_km": None if body.scope == "all" else body.distance_km + 60,
                          "level": level, "wave_m": body.wave_m if body.tsunami else None})
    st.store.audit(user["username"], "simulate_alert", {"id": a["id"], "area": c.id, "scope": body.scope})
    return _json(a, 201)


@app.post("/api/alerts/{aid}/end")
def api_end_alert(aid: str, body: EndAlertIn, user: dict = Depends(require("petugas", "perencana")),
                  st: AppState = Depends(get_state)):
    a = st.store.get_alert(aid)
    if a is None:
        raise HTTPException(404, "Peringatan tidak ditemukan.")
    if a["status"] != "aktif":
        raise HTTPException(409, "Peringatan ini sudah berakhir.")
    a = st.store.end_alert(aid, user["name"], body.note)
    st.store.audit(user["username"], "end_alert", {"id": aid, "note": body.note})
    st.recompute_all("alert_end")
    st.broker.publish("alert", public_alert(a))
    st.push_broadcast({"type": "end", "title": "Peringatan dicabut", "body": f"{body.note} — oleh petugas",
                       "tag": aid, "url": "/"},
                      center=(a["lat"], a["lon"]) if a.get("radius_km") is not None else None,
                      radius_km=a.get("radius_km"))
    return _json(a)


# ---- simulasi pribadi (untuk mencoba/menilai di lokasi sendiri) ----
@app.post("/api/sim/self")
def api_self_sim(body: SelfSimIn, st: AppState = Depends(get_state)):
    issued = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=body.delay_s)
    txt = guidance.alert_text(tsunami=True, magnitude=7.5, region="lepas pantai terdekat dari lokasi Anda",
                              eta_min=20, simulated=True)
    sim = {"id": "self-" + hashlib.sha1(f"{time.time()}{body.lat}".encode()).hexdigest()[:10], "source": "SIMULASI",
           "event": "Tsunami", "magnitude": 7.5, "depth_km": 15, "lat": body.lat, "lon": body.lon,
           "region": "lepas pantai terdekat dari lokasi Anda", "tsunami": True,
           "issued_at": issued.isoformat(timespec="seconds"), "eta_min": 20, "headline": txt["headline"],
           "instruction": txt["instruction"], "self": True}
    pushed = False
    if body.endpoint and st.push:
        row = st.store.get_sub(body.endpoint)
        if row:
            payload = _alert_payload(sim, "sim")
            threading.Timer(body.delay_s, lambda: st.push_send(row, payload)).start()
            pushed = True
    return _json({"sim": sim, "push": pushed})


# ---- Web Push ----
@app.get("/api/push/key")
def api_push_key(st: AppState = Depends(get_state)):
    return _json({"enabled": bool(st.push), "key": st.push.public_b64 if st.push else None})


@app.post("/api/push/subscribe")
def api_push_subscribe(body: SubIn, st: AppState = Depends(get_state)):
    sub = body.subscription
    if not st.push:
        raise HTTPException(503, "Notifikasi push belum aktif di server ini.")
    if not isinstance(sub.get("endpoint"), str) or not sub["endpoint"].startswith("https://") \
            or not (sub.get("keys") or {}).get("p256dh") or not (sub.get("keys") or {}).get("auth"):
        raise HTTPException(400, "Data langganan tidak valid.")
    st.store.upsert_sub(sub["endpoint"], {"endpoint": sub["endpoint"], "keys": sub["keys"]}, body.lat, body.lon)
    return _json({"ok": True})


@app.post("/api/push/unsubscribe")
def api_push_unsubscribe(body: EndpointIn, st: AppState = Depends(get_state)):
    st.store.delete_sub(body.endpoint)
    return _json({"ok": True})


@app.post("/api/push/test")
def api_push_test(body: EndpointIn, st: AppState = Depends(get_state)):
    row = st.store.get_sub(body.endpoint)
    if not row:
        raise HTTPException(404, "HP ini belum terdaftar untuk notifikasi.")
    code = st.push_send(row, {"type": "test", "title": "SEMPAT: notifikasi aktif",
                              "body": "Uji berhasil. Peringatan tsunami akan muncul seperti ini.",
                              "tag": "sempat-test", "url": "/"})
    if code in (200, 201, 202):
        return _json({"ok": True})
    raise HTTPException(502, f"Layanan push menolak pesan (kode {code}). Coba aktifkan ulang notifikasi.")


# ---- shelter terdaftar ----
@app.post("/api/shelters/{bid}")
def api_register_shelter(bid: str, area: Optional[str] = None, user: dict = Depends(require("perencana")),
                         st: AppState = Depends(get_state)):
    c = st.get(area)
    i = c.engine.bindex.get(bid)
    if i is None:
        raise HTTPException(404, "Bangunan tidak ditemukan.")
    ok, why = tsunami.can_register(c.engine.buildings[i])  # satu aturan tier yang sama dengan rute & optimizer
    if not ok:
        raise HTTPException(400, f"Bangunan ini belum bisa menjadi shelter resmi. {why}.")
    st.store.register_shelter(c.id, bid, user["username"])
    st.store.audit(user["username"], "register_shelter", {"id": bid, "area": c.id})
    st.recompute(c.id, "shelters")
    return _json({"ok": True})


@app.delete("/api/shelters/{bid}")
def api_unregister_shelter(bid: str, area: Optional[str] = None, user: dict = Depends(require("perencana")),
                           st: AppState = Depends(get_state)):
    c = st.get(area)
    st.store.unregister_shelter(c.id, bid)
    st.store.audit(user["username"], "unregister_shelter", {"id": bid, "area": c.id})
    st.recompute(c.id, "shelters")
    return _json({"ok": True})


# ---- okupansi & alokasi kapasitas (petugas/perencana) ----
@app.put("/api/shelters/{bid}/occupancy")
def api_set_occupancy(bid: str, body: OccupancyIn, user: dict = Depends(require("petugas", "perencana")),
                      st: AppState = Depends(get_state)):
    """Petugas melaporkan jumlah orang yang SUDAH berada di gedung: kapasitas tersedia = kapasitas - okupansi."""
    c = st.get(body.area_id)
    if bid not in c.engine.bindex:
        raise HTTPException(404, "Bangunan tidak ditemukan.")
    occ = st.occupancy(c)
    if body.occupied > 0:
        occ[bid] = body.occupied
    else:
        occ.pop(bid, None)
    st.store.set_kv(f"occ:{c.id}", json.dumps(occ))
    st.store.audit(user["username"], "set_occupancy", {"id": bid, "area": c.id, "occupied": body.occupied})
    st.recompute(c.id, "okupansi")
    return _json({"ok": True, "occupied": body.occupied})


@app.get("/api/allocation")
def api_allocation(area: Optional[str] = None, mode: str = Query("aman", pattern="^(aman|gedung)$"),
                   user: dict = Depends(require("petugas", "perencana")), st: AppState = Depends(get_state)):
    """Rincian alokasi (per bangunan & kelompok) untuk audit. Hanya hasil yang sudah dihitung; tidak menghitung."""
    c = st.get(area)
    alert = st.alert_for(c)
    H = c.engine.wave(st.wave_for(c, alert))
    dests, _ = c.engine.mode_dests(mode, shelter_ids=st.shelter_ids(c), H=H, at=st.at_for(c, alert),
                                   occupancy=st.occupancy(c))
    out = c.engine.alloc_cached(c.engine.alloc_key(mode, dests, st.closures(c), H,
                                                   c.engine.budget_s((alert or {}).get("eta_min"))))
    tb = json.loads(c.tables_bytes or b"{}")
    view = (tb.get("capacity") or {}).get(mode)
    if out is None or out.get("status") == "gagal" or "origins" not in out:
        return _json({"status": (view or {}).get("status", "pending"), "capacity": view, "rows": []})
    rows = []
    budget = c.engine.budget_s((alert or {}).get("eta_min"))
    for oi, f in out["result"]["flows"].items():
        bi, g, cands, fb = out["origins"][oi]
        for d, n in f.items():
            if n <= 0:
                continue
            t = None if d == -2 else (fb if d == -1 else cands[d])
            rows.append({"building": c.engine.buildings[bi]["id"], "group": g, "people": n,
                         "dest": dests[d]["id"] if d >= 0 else ("zona_aman_tanah_tinggi" if d == -1 else None),
                         "dest_type": dests[d]["type"] if d >= 0 else ("tanpa_batas_tercatat" if d == -1 else "tidak_teralokasi"),
                         "time_s": None if t is None else round(t, 1),
                         "late": t is not None and t > budget})
    return _json({"status": out["status"], "capacity": view, "rows": rows})


# ---- perencanaan ----
@app.post("/api/plan")
def api_plan(body: PlanIn, user: dict = Depends(require("perencana")), st: AppState = Depends(get_state)):
    c = st.get(body.area_id)
    shelters = st.shelter_ids(c) if body.use_registered else []
    r = c.engine.plan(eta_min=body.eta_min, delay_min=body.delay_min, margin_min=body.margin_min,
                      shelter_ids=shelters, closures=st.closures(c), optimize_k=body.optimize_k,
                      design_height_m=body.design_height_m, at=_plan_time(st, c, body))
    r["orphans"] = c.orphans  # survei/shelter yang merujuk gedung yang tidak ada lagi (tidak ditautkan ulang)
    return _json(r)


def _plan_time(st: AppState, c: AreaCtx, body: PlanIn):
    """Waktu skenario perencanaan -> tsunami.WORST_CASE atau AlertTime (jendela = eta skenario)."""
    mode = body.scenario_time
    if mode == "terburuk":
        return tsunami.WORST_CASE
    if mode == "peringatan":
        alert = st.alert_for(c)
        if not alert:
            raise HTTPException(400, "Tidak ada peringatan aktif untuk area ini; pilih jam skenario lain.")
        return st.at_for(c, alert)
    if mode == "sekarang":
        return tsunami.alert_time(time.time(), c.tz, body.eta_min * 60.0)
    hh, mm = (int(x) for x in mode.split(":"))
    off = c.tz["offsets"][0]
    local_now = time.time() + off * 3600.0
    day0 = math.floor(local_now / 86400.0) * 86400.0
    if body.scenario_day is not None:  # hari dalam minggu (Senin = 0); 1 Jan 1970 = Kamis
        day0 += ((body.scenario_day - (int(day0 // 86400) + 3) % 7) % 7) * 86400.0
    return tsunami.alert_time(day0 + hh * 3600.0 + mm * 60.0 - off * 3600.0, c.tz, body.eta_min * 60.0)


@app.post("/api/point-info")
def api_point_info(body: PointInfoIn, user: dict = Depends(require("perencana")), st: AppState = Depends(get_state)):
    c = st.get(body.area_id)
    info = c.engine.point_info(body.lat, body.lon, closures=st.closures(c), shelter_ids=st.shelter_ids(c))
    place = f"titik {body.lat:.5f}, {body.lon:.5f}"
    note = "; ".join(c.meta.get("sources") or []) or "data terbuka OSM, elevasi SRTM"
    text = guidance.template_guidance(info, body.eta_min, place, note)
    llm_text = guidance.llm_rewrite(text) if body.llm else None
    return _json({"info": info, "guidance": llm_text or text, "guidance_source": "llm" if llm_text else "templat"})


# ---- gedung: kelayakan evakuasi vertikal & survei petugas ----
def _building_view(c: AreaCtx, b: dict, wave: Optional[float], at=None, internal: bool = True) -> dict:
    """internal=False: tanpa nama petugas/catatan survei (lihat public_building)."""
    src = b if internal else public_building(b)
    view = {k: v for k, v in src.items() if k not in ("poly", "_auto")}
    ok, why = tsunami.eligible(b)
    tier, cv = tsunami.verification(b)
    scen = {lv: c.engine.assess(b, tsunami.wave_height_for(lv, None, c.engine.design_wave_m)) for lv in tsunami.LEVELS}
    acc, acc_why = tsunami.access_state(b, at)
    return {"building": view, "eligible": ok, "why": why, "tier": tier, "tier_label": tsunami.TIER_LABEL[tier],
            "caveats": cv, "now": c.engine.assess(b, wave, at),
            "access_now": {"state": acc, "note": acc_why, "time": tsunami.local_time_label(at) if at else None,
                           "tz_src": c.tz["src"]},
            "levels": scen, "design_m": c.engine.design_wave_m,
            "height_per_level_m": c.area["params"].get("height_per_level_m"),
            "source_label": tsunami.LEVEL_SOURCES.get(b.get("levels_src", "default"), b.get("levels_src"))}


@app.get("/api/buildings/{bid}")
def api_building(bid: str, area: Optional[str] = None, user: Optional[dict] = Depends(current_user),
                 st: AppState = Depends(get_state)):
    c = st.get(area)
    i = c.engine.bindex.get(bid)
    if i is None:
        raise HTTPException(404, "Bangunan tidak ditemukan.")
    alert = st.alert_for(c)
    return _json(_building_view(c, c.engine.buildings[i], st.wave_for(c, alert), st.at_for(c, alert),
                                internal=user is not None))


@app.put("/api/buildings/{bid}")
def api_survey_building(bid: str, body: SurveyIn, user: dict = Depends(require("petugas", "perencana")),
                        st: AppState = Depends(get_state)):
    c = st.get(body.area_id)
    if bid not in c.engine.bindex:
        raise HTTPException(404, "Bangunan tidak ditemukan.")
    fields = body.model_dump(exclude={"area_id"})
    if body.levels is not None and not body.levels_confirmed:
        raise HTTPException(400, "Jumlah lantai hanya disimpan sebagai hasil survei bila dihitung langsung di lokasi: "
                                 "centang konfirmasi, atau kosongkan isian jumlah lantai.")
    if (body.ground_elev_m is None) != (body.ground_elev_method is None):
        raise HTTPException(400, "Elevasi tanah terukur wajib diisi bersama metode pengukurannya (dan sebaliknya).")
    if body.access == "jam_kerja":
        hours = tsunami.parse_open_hours(body.open_hours)
        if not hours:
            raise HTTPException(400, "Akses terbatas: isi jam buka gedung yang sebenarnya, mis. 07:00-16:00 "
                                     "(beberapa rentang dipisah koma).")
        fields["open_hours"] = ", ".join(f"{a // 60:02d}:{a % 60:02d}-{e // 60:02d}:{e % 60:02d}" for a, e in hours)
        fields["open_days"] = "".join(sorted(set(body.open_days))) if body.open_days else None
    else:
        fields["open_hours"] = fields["open_days"] = None
    info = st.store.set_building_info(c.id, bid, fields, user["name"])
    with c.lock:
        b = c.apply_survey(info)
        c.refresh_bytes(changed=[bid])
        rev = c.rev
    st.store.audit(user["username"], "survey_building", {"id": bid, "area": c.id, **body.model_dump(exclude={"area_id"})})
    st.recompute(c.id, "survey")
    st.broker.publish("area", {"area": c.id, "reason": "survey", "rev": rev, "epoch": c.epoch, "ids": [bid]})
    alert = st.alert_for(c)
    return _json(_building_view(c, b, st.wave_for(c, alert), st.at_for(c, alert)))


@app.delete("/api/buildings/{bid}/survey")
def api_clear_survey(bid: str, area: Optional[str] = None, user: dict = Depends(require("petugas", "perencana")),
                     st: AppState = Depends(get_state)):
    c = st.get(area)
    st.store.delete_building_info(c.id, bid)
    with c.lock:
        c.clear_survey(bid)
        c.refresh_bytes(changed=[bid])
        rev = c.rev
    st.store.audit(user["username"], "clear_survey", {"id": bid, "area": c.id})
    st.recompute(c.id, "survey")
    st.broker.publish("area", {"area": c.id, "reason": "survey", "rev": rev, "epoch": c.epoch, "ids": [bid]})
    return _json({"ok": True})


def _reload_area(st: AppState, aid: str, reason: str) -> None:
    st.recompute(aid, reason)
    st.broker.publish("area", {"area": aid, "reason": reason})


@app.post("/api/areas/{aid}/hazard")
def api_upload_hazard(aid: str, body: GeoUploadIn, user: dict = Depends(require("perencana")),
                      st: AppState = Depends(get_state)):
    c = st.get(aid)
    rings = heights.parse_hazard_polygons(body.geojson)
    if not rings:
        raise HTTPException(400, "Berkas tidak berisi poligon (Polygon/MultiPolygon) zona bahaya.")
    area = _automatic_layer(c)
    name = (body.name or "peta bahaya resmi (unggahan)")[:120]
    try:
        r = area_builder.apply_hazard_polygons(area, rings, name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if r["safe_nodes"] == 0:
        raise HTTPException(400, "Setelah peta ini dipakai, tidak ada jalan yang berada di luar zona bahaya. "
                                 "Periksa berkas (koordinat harus lon,lat WGS84) atau perluas area.")
    _record_layer(st, area, "hazard", "upload", name, {"rings": rings}, user)
    _save_keep_surveys(st, area)
    st.store.audit(user["username"], "upload_hazard", {"area": aid, "polygons": len(rings), **r})
    _reload_area(st, aid, "hazard")
    return _json({"ok": True, "polygons": len(rings), **r})


# ---- grid penduduk (mis. Meta/CIESIN HRSL ±30 m, CC BY 4.0) ----
_GID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")


def _drop_ctx_covering(st: AppState, bbox) -> List[str]:
    """Muat ulang area yang tercakup grid (penduduk & alokasi dihitung ulang)."""
    w, s, e, n = bbox
    hit = [aid for aid, m in st.index.items()
           if not (m["bbox"][2] < w or m["bbox"][0] > e or m["bbox"][3] < s or m["bbox"][1] > n)]
    with st.lock:
        for aid in hit:
            st.ctx.pop(aid, None)
    for aid in hit:
        st.broker.publish("area", {"area": aid, "reason": "population"})
    return hit


@app.get("/api/population")
def api_population_list(user: dict = Depends(require("petugas", "perencana")), st: AppState = Depends(get_state)):
    return _json({"grids": [population.summary(g) for g in st.grids.all()]})


@app.post("/api/population")
async def api_population_upload(request: Request, gid: str = Query(..., min_length=2, max_length=41),
                                name: str = Query(..., min_length=2, max_length=120),
                                source: str = Query("Meta/CIESIN High Resolution Population Density Maps",
                                                    max_length=200),
                                license: str = Query("CC BY 4.0", max_length=60), year: Optional[str] = Query(None),
                                synthetic: bool = Query(False),
                                user: dict = Depends(require("perencana")), st: AppState = Depends(get_state)):
    """Unggah grid penduduk sebagai CSV mentah (badan permintaan = isi CSV, mis. ekspor HRSL)."""
    if not _GID_RE.match(gid):
        raise HTTPException(400, "ID grid hanya huruf kecil, angka, - dan _.")
    raw = await request.body()
    if len(raw) > 200 * 1024 * 1024:
        raise HTTPException(413, "Berkas terlalu besar (> 200 MB); potong dulu ke wilayah kerja.")
    try:
        cells = population.parse_csv(raw.decode("utf-8-sig", errors="replace"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    g = population.make_grid(cells, gid=gid, name=name, source=source, license=license, year=year,
                             synthetic=synthetic)
    sm = st.grids.save(g)
    hit = _drop_ctx_covering(st, g["bbox"])
    st.store.audit(user["username"], "population_upload", {"id": gid, "cells": g["count"], "total": g["total"]})
    return _json({"ok": True, "grid": sm, "areas": hit}, 201)


@app.delete("/api/population/{gid}")
def api_population_delete(gid: str, user: dict = Depends(require("perencana")), st: AppState = Depends(get_state)):
    g = next((x for x in st.grids.all() if x["id"] == gid), None)
    if g is None:
        raise HTTPException(404, "Grid tidak ditemukan.")
    st.grids.delete(gid)
    hit = _drop_ctx_covering(st, g["bbox"])
    st.store.audit(user["username"], "population_delete", {"id": gid})
    return _json({"ok": True, "areas": hit})


@app.get("/api/areas/{aid}/population")
def api_area_population(aid: str, user: dict = Depends(require("petugas", "perencana")),
                        st: AppState = Depends(get_state)):
    """Sel grid penduduk di area untuk peta kepadatan. Kosong bila hanya model orang/rumah (tidak pernah dibuat
    peta kepadatan dari model)."""
    c = st.get(aid)
    cells = population.density_cells(c.area, c.grids)
    return _json({"population": c.meta.get("population"), "cells": cells})


@app.get("/api/areas/{aid}/time-grid")
def api_time_grid(aid: str, mode: str = Query("aman", pattern="^(aman|gedung)$"),
                  group: str = Query("umum", pattern="^(umum|rentan)$"), cell_m: float = Query(30.0, ge=10, le=200),
                  user: dict = Depends(require("petugas", "perencana")), st: AppState = Depends(get_state)):
    """Grid waktu evakuasi (bawaan sel 30 m, pita 3 menit) dari tabel HP saat ini (sama dengan rute warga).
    Metode & keterbatasan: backend/mapgrid.py. Resolusi sel bukan akurasi waktu."""
    c = st.get(aid)
    key = (c.version, mode, group, cell_m)
    cache = getattr(c, "_tgrid", None)
    if cache and cache[0] == key:
        return _json(cache[1])
    tb = json.loads(c.tables_bytes or b"{}")
    tab = (tb.get("tables") or {}).get(f"{mode}-{group}")
    if not tab:
        raise HTTPException(409, "Tabel rute belum siap.")
    from .speed import GROUP_SPEED_MPS
    lbl = (f"Waktu jalan kaki ke {'zona aman/shelter resmi' if mode == 'aman' else 'zona aman, tanah tinggi, atau gedung terverifikasi'}"
           f" ({'umum 1,0 m/s' if group == 'umum' else 'rentan 0,5 m/s'}).")
    g = mapgrid.time_grid(c.area, tab, speed_mps=GROUP_SPEED_MPS[group], cell_m=cell_m, label=lbl)
    g.update(version=c.version, mode=mode, group=group, capacity_status=((tb.get("capacity") or {}).get(mode) or {}).get("status"))
    c._tgrid = (key, g)
    return _json(g)


@app.get("/api/areas/{aid}/evidence")
def api_evidence(aid: str, eta_min: float = Query(20, ge=1, le=240), wave_m: Optional[float] = Query(None, ge=0.5, le=40),
                 delay_min: float = Query(5, ge=0, le=120), margin_min: float = Query(2, ge=0, le=60),
                 user: dict = Depends(require("perencana")), st: AppState = Depends(get_state)):
    """Tsunami Ready Evidence Pack (bukti pendukung per indikator, provenance, keterbatasan). Halaman cetak:
    /perencana/bukti. Bukan penilaian status Tsunami Ready."""
    c = st.get(aid)
    out = evidence.build(c, st, eta_min=eta_min, wave_m=wave_m, delay_min=delay_min, margin_min=margin_min)
    out["generated_at"] = now_iso()
    out["generated_by"] = user.get("name") or user["username"]
    st.store.audit(user["username"], "evidence_pack", {"area": c.id, "eta_min": eta_min, "wave_m": out["scenario"]["wave_m"]})
    return _json(out)


@app.post("/api/areas/{aid}/heights")
def api_upload_heights(aid: str, body: GeoUploadIn, user: dict = Depends(require("perencana")),
                       st: AppState = Depends(get_state)):
    c = st.get(aid)
    feats = heights.parse_height_features(body.geojson)
    if not feats:
        raise HTTPException(400, "Tidak ada fitur dengan properti height (meter) atau levels (jumlah lantai).")
    area = _automatic_layer(c)
    surveyed = _surveyed_snapshot(st, c, area)
    stats = heights.apply_heights(area, c.engine, feats, source="upload")  # Google hanya dari penanda per fitur
    stats["masked_by_survey"] = _masked_count(surveyed, area)
    stats["google_features"] = sum(1 for f in feats if f.get("source") == "google")
    note = f"Tinggi gedung: {(body.name or 'unggahan perencana')[:100]}"
    heights.add_source_note(area, note)
    _record_layer(st, area, "heights", "upload", body.name or "unggahan perencana", {"features": feats, "note": note},
                  user)
    _save_keep_surveys(st, area)
    st.store.audit(user["username"], "upload_heights", {"area": aid, **stats})
    _reload_area(st, aid, "heights")
    return _json({"ok": True, **stats})


@app.post("/api/areas/{aid}/heights/ee")
def api_ee_heights(aid: str, body: EEIn, user: dict = Depends(require("perencana")),
                   st: AppState = Depends(get_state)):
    ok, why = heights.ee_available()
    if not config.EE_PROJECT:
        raise HTTPException(503, "Earth Engine belum diatur: isi SEMPAT_EE_PROJECT dengan ID proyek Google Cloud "
                                 "yang terdaftar di Earth Engine, lalu jalankan 'earthengine authenticate'.")
    if not ok:
        raise HTTPException(503, why)
    c = st.get(aid)
    area = _automatic_layer(c)
    try:
        feats = heights.ee_fetch_heights(area, project=config.EE_PROJECT, year=body.year,
                                         add_missing=body.add_missing)
    except Exception as exc:
        raise HTTPException(502, f"Earth Engine gagal: {str(exc)[:300]}")
    surveyed = _surveyed_snapshot(st, c, area)
    stats = heights.apply_heights(area, c.engine, feats, source="google")
    stats["masked_by_survey"] = _masked_count(surveyed, area)
    note = f"Tinggi gedung: Google Open Buildings 2.5D Temporal {body.year} (CC BY 4.0)"
    heights.add_source_note(area, note)
    _record_layer(st, area, "ee", "google", f"Earth Engine {body.year}", {"features": feats, "note": note}, user)
    _save_keep_surveys(st, area)
    st.store.audit(user["username"], "ee_heights", {"area": aid, "year": body.year, **stats})
    _reload_area(st, aid, "heights")
    return _json({"ok": True, **stats})


def _record_layer(st: AppState, area: dict, kind: str, source: str, name: str, data: dict, user: dict) -> None:
    """Simpan lapisan unggahan di DB dan tandai sudah diterapkan pada area ini (diterapkan ulang setelah rebuild)."""
    lid = st.store.add_layer(area["meta"]["id"], kind, source, name[:120], data, user["username"])
    area["meta"]["layers_applied"] = list(area["meta"].get("layers_applied") or []) + [lid]


@app.put("/api/areas/{aid}/timezone")
def api_set_timezone(aid: str, body: TimezoneIn, user: dict = Depends(require("perencana")),
                     st: AppState = Depends(get_state)):
    """Tetapkan zona waktu area secara eksplisit (dipakai untuk jam buka gedung; menggantikan perkiraan dari bujur)."""
    if not tsunami.tz_offsets(body.timezone):
        raise HTTPException(400, "Zona waktu tidak dikenal.")
    c = st.get(aid)
    st.store.set_kv(f"tz:{c.id}", body.timezone)
    with c.lock:
        c.meta.update(timezone=body.timezone, timezone_src="perencana")
        c.tz = tsunami.area_timezone(c.meta)
        c.refresh_bytes()
    st.store.audit(user["username"], "set_timezone", {"area": c.id, "timezone": body.timezone})
    _reload_area(st, c.id, "timezone")
    return _json({"ok": True, "timezone": c.tz})


def _automatic_layer(c: AreaCtx) -> dict:
    """Salinan area TANPA survei. Unggahan/Google diterapkan ke lapisan ini sesuai peringkat sumber, sehingga data
    yang masuk saat gedung tersurvei tetap tersimpan dan muncul lagi bila survei dibatalkan (survei tetap menang
    selama aktif karena diterapkan ulang dari database saat area dimuat)."""
    area = json.loads(c.area_bytes)
    for b in area["buildings"]:
        _strip_survey(b)
    return area


def _surveyed_snapshot(st: AppState, c: AreaCtx, area: dict) -> dict:
    ids = {x["building_id"] for x in st.store.list_building_info(c.id)}
    return {b["id"]: (b.get("levels"), b.get("levels_src")) for b in area["buildings"] if b["id"] in ids}


def _masked_count(before: dict, area: dict) -> int:
    """Jumlah gedung tersurvei yang data otomatisnya berubah (tersimpan, tapi tertutup survei sampai dibatalkan)."""
    now = {b["id"]: (b.get("levels"), b.get("levels_src")) for b in area["buildings"] if b["id"] in before}
    return sum(1 for k, v in now.items() if v != before[k])


def _save_keep_surveys(st: AppState, area: dict) -> None:
    """Simpan area tanpa nilai survei (survei disimpan terpisah dan diterapkan ulang saat dimuat)."""
    for b in area["buildings"]:
        _strip_survey(b)
    st.save_area(area)


@app.post("/api/demo/reset")
def api_demo_reset(user: dict = Depends(require("petugas", "perencana")), st: AppState = Depends(get_state)):
    for a in st.store.active_alerts():
        st.store.end_alert(a["id"], user["name"], "Reset demo")
    st.store.delete_simulated_alerts()  # riwayat latihan dihapus agar warga kembali ke status normal
    for aid in list(st.index):
        for p in st.store.list_points(aid):
            if not p["sample"]:
                st.store.delete_point(p["id"])
            else:
                st.store.update_point(p["id"], {"status": "kosong" if "SAR" in p["name"] else "agak_ramai"}, "sistem")
        for cl in st.store.list_closures(aid):
            st.store.delete_closure(cl["id"])
    st.store.audit(user["username"], "demo_reset", {})
    st.recompute_all("reset")
    st.broker.publish("alert", {"status": "reset"})
    return _json({"ok": True})


# ---------------------------------------------------------------------------
# Frontend statis
# ---------------------------------------------------------------------------
FRONT = config.FRONTEND_DIR
app.mount("/static", StaticFiles(directory=str(FRONT)), name="static")

_NO_CACHE = {"Cache-Control": "no-cache"}


@app.get("/")
def page_warga():
    return FileResponse(FRONT / "index.html", headers=_NO_CACHE)


@app.get("/petugas")
def page_petugas():
    return FileResponse(FRONT / "petugas.html", headers=_NO_CACHE)


@app.get("/perencana")
def page_perencana():
    return FileResponse(FRONT / "perencana.html", headers=_NO_CACHE)


@app.get("/perencana/bukti")
def page_evidence():
    return FileResponse(FRONT / "bukti.html", headers=_NO_CACHE)


@app.get("/sw.js")
def service_worker():
    return FileResponse(FRONT / "sw.js", media_type="application/javascript",
                        headers={**_NO_CACHE, "Service-Worker-Allowed": "/"})


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(FRONT / "manifest.webmanifest", media_type="application/manifest+json")
