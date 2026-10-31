"""Penyimpanan SQLite untuk peringatan, titik bantuan, jalan putus, shelter terdaftar, survei gedung, lapisan data
unggahan (tinggi gedung, peta bahaya, Earth Engine) dan log audit.

Lapisan unggahan disimpan di sini (bukan hanya di area.json) agar tidak hilang saat area dibangun ulang dari OSM.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
  id TEXT PRIMARY KEY, source TEXT, event TEXT, magnitude REAL, depth_km REAL,
  lat REAL, lon REAL, region TEXT, tsunami INTEGER, issued_at TEXT, eta_min REAL,
  status TEXT, headline TEXT, instruction TEXT, ended_at TEXT, ended_by TEXT, ended_note TEXT, raw TEXT
);
CREATE TABLE IF NOT EXISTS points (
  id TEXT PRIMARY KEY, area_id TEXT, kind TEXT, name TEXT, lat REAL, lon REAL, status TEXT,
  services TEXT, capacity INTEGER, note TEXT, sample INTEGER DEFAULT 0,
  created_by TEXT, created_at TEXT, updated_by TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS closures (
  id TEXT PRIMARY KEY, area_id TEXT, edge INTEGER, lat REAL, lon REAL, note TEXT,
  created_by TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS shelters (
  area_id TEXT, building_id TEXT, registered_by TEXT, registered_at TEXT,
  PRIMARY KEY (area_id, building_id)
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, user TEXT, action TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS push_subs (
  endpoint TEXT PRIMARY KEY, sub TEXT, lat REAL, lon REAL, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS building_info (
  area_id TEXT, building_id TEXT, levels INTEGER, basement INTEGER, structure TEXT, access TEXT,
  roof INTEGER, slf INTEGER, note TEXT, updated_by TEXT, updated_at TEXT,
  PRIMARY KEY (area_id, building_id)
);
CREATE TABLE IF NOT EXISTS area_layers (
  id TEXT PRIMARY KEY, area_id TEXT, kind TEXT, source TEXT, name TEXT, data TEXT,
  created_by TEXT, created_at TEXT
);
"""
# Kolom yang ditambahkan setelah versi awal (migrasi ringan)
MIGRATIONS = [("alerts", "radius_km", "REAL"), ("alerts", "created_by", "TEXT"),
              ("alerts", "level", "TEXT"), ("alerts", "wave_m", "REAL"),
              # jam buka gedung hasil survei (akses "jam_kerja"); baris lama = NULL = jam buka belum dicatat
              ("building_info", "open_hours", "TEXT"), ("building_info", "open_days", "TEXT"),
              # audit D-7/D-2: konfirmasi jumlah lantai dihitung langsung, kondisi bangunan, elevasi tanah terukur
              ("building_info", "levels_confirmed", "INTEGER"), ("building_info", "condition", "TEXT"),
              ("building_info", "ground_elev_m", "REAL"), ("building_info", "ground_elev_method", "TEXT")]


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            for table, col, typ in MIGRATIONS:
                cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if col not in cols:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            self.conn.commit()

    # ---------- util ----------
    def _q(self, sql: str, args=()) -> List[dict]:
        with self.lock:
            cur = self.conn.execute(sql, args)
            rows = [dict(r) for r in cur.fetchall()]
        return rows

    def _x(self, sql: str, args=()) -> None:
        with self.lock:
            self.conn.execute(sql, args)
            self.conn.commit()

    def audit(self, user: str, action: str, detail: dict) -> None:
        self._x("INSERT INTO audit(at,user,action,detail) VALUES (?,?,?,?)",
                (now_iso(), user, action, json.dumps(detail, ensure_ascii=False)))

    def get_kv(self, k: str) -> Optional[str]:
        r = self._q("SELECT v FROM kv WHERE k=?", (k,))
        return r[0]["v"] if r else None

    def set_kv(self, k: str, v: str) -> None:
        self._x("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

    # ---------- alerts ----------
    def add_alert(self, a: dict) -> dict:
        a = dict(a)
        a.setdefault("id", uuid.uuid4().hex[:12])
        a.setdefault("issued_at", now_iso())
        a.setdefault("status", "aktif")
        cols = ["id", "source", "event", "magnitude", "depth_km", "lat", "lon", "region", "tsunami",
                "issued_at", "eta_min", "status", "headline", "instruction", "raw", "radius_km", "created_by",
                "level", "wave_m"]
        self._x(f"INSERT INTO alerts({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                tuple(a.get(c) for c in cols))
        return self.get_alert(a["id"])

    def get_alert(self, aid: str) -> Optional[dict]:
        r = self._q("SELECT * FROM alerts WHERE id=?", (aid,))
        return _alert_out(r[0]) if r else None

    def active_alert(self) -> Optional[dict]:
        r = self._q("SELECT * FROM alerts WHERE status='aktif' ORDER BY issued_at DESC LIMIT 1")
        return _alert_out(r[0]) if r else None

    def active_alerts(self) -> List[dict]:
        return [_alert_out(r) for r in self._q("SELECT * FROM alerts WHERE status='aktif' ORDER BY issued_at DESC")]

    def latest_alerts(self, limit: int = 10) -> List[dict]:
        return [_alert_out(r) for r in self._q("SELECT * FROM alerts ORDER BY issued_at DESC LIMIT ?", (limit,))]

    def latest_alert(self) -> Optional[dict]:
        r = self._q("SELECT * FROM alerts ORDER BY issued_at DESC LIMIT 1")
        return _alert_out(r[0]) if r else None

    def list_alerts(self, limit: int = 20) -> List[dict]:
        return [_alert_out(r) for r in self._q("SELECT * FROM alerts ORDER BY issued_at DESC LIMIT ?", (limit,))]

    def delete_simulated_alerts(self) -> None:
        self._x("DELETE FROM alerts WHERE source='SIMULASI'")

    def end_alert(self, aid: str, by: str, note: str) -> Optional[dict]:
        self._x("UPDATE alerts SET status='berakhir', ended_at=?, ended_by=?, ended_note=? WHERE id=? AND status='aktif'",
                (now_iso(), by, note, aid))
        return self.get_alert(aid)

    # ---------- langganan notifikasi (Web Push) ----------
    def upsert_sub(self, endpoint: str, sub: dict, lat: Optional[float], lon: Optional[float]) -> None:
        t = now_iso()
        self._x("INSERT INTO push_subs(endpoint,sub,lat,lon,created_at,updated_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(endpoint) DO UPDATE SET sub=excluded.sub, "
                "lat=COALESCE(excluded.lat, push_subs.lat), lon=COALESCE(excluded.lon, push_subs.lon), "
                "updated_at=excluded.updated_at", (endpoint, json.dumps(sub), lat, lon, t, t))

    def get_sub(self, endpoint: str) -> Optional[dict]:
        r = self._q("SELECT * FROM push_subs WHERE endpoint=?", (endpoint,))
        return r[0] if r else None

    def list_subs(self) -> List[dict]:
        return self._q("SELECT * FROM push_subs")

    def delete_sub(self, endpoint: str) -> None:
        self._x("DELETE FROM push_subs WHERE endpoint=?", (endpoint,))

    # ---------- points ----------
    def list_points(self, area_id: str) -> List[dict]:
        return [_point_out(r) for r in self._q("SELECT * FROM points WHERE area_id=? ORDER BY created_at", (area_id,))]

    def get_point(self, pid: str) -> Optional[dict]:
        r = self._q("SELECT * FROM points WHERE id=?", (pid,))
        return _point_out(r[0]) if r else None

    def add_point(self, area_id: str, p: dict, by: str) -> dict:
        pid = uuid.uuid4().hex[:10]
        t = now_iso()
        self._x("INSERT INTO points(id,area_id,kind,name,lat,lon,status,services,capacity,note,sample,created_by,"
                "created_at,updated_by,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, area_id, p["kind"], p["name"], p["lat"], p["lon"], p["status"],
                 json.dumps(p.get("services", [])), p.get("capacity"), p.get("note", ""),
                 1 if p.get("sample") else 0, by, t, by, t))
        return self.get_point(pid)

    def update_point(self, pid: str, fields: dict, by: str) -> Optional[dict]:
        allowed = {"kind", "name", "lat", "lon", "status", "services", "capacity", "note"}
        sets, args = [], []
        for k, v in fields.items():
            if k in allowed and v is not None:
                sets.append(f"{k}=?")
                args.append(json.dumps(v) if k == "services" else v)
        sets += ["updated_by=?", "updated_at=?"]
        args += [by, now_iso(), pid]
        self._x(f"UPDATE points SET {','.join(sets)} WHERE id=?", tuple(args))
        return self.get_point(pid)

    def delete_point(self, pid: str) -> None:
        self._x("DELETE FROM points WHERE id=?", (pid,))

    # ---------- closures ----------
    def list_closures(self, area_id: str) -> List[dict]:
        return self._q("SELECT * FROM closures WHERE area_id=? ORDER BY created_at", (area_id,))

    def add_closure(self, area_id: str, edge: int, lat: float, lon: float, note: str, by: str) -> dict:
        cid = uuid.uuid4().hex[:10]
        self._x("INSERT INTO closures(id,area_id,edge,lat,lon,note,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (cid, area_id, edge, lat, lon, note, by, now_iso()))
        return self._q("SELECT * FROM closures WHERE id=?", (cid,))[0]

    def delete_closure(self, cid: str) -> None:
        self._x("DELETE FROM closures WHERE id=?", (cid,))

    # ---------- shelters ----------
    def list_shelters(self, area_id: str) -> List[dict]:
        return self._q("SELECT * FROM shelters WHERE area_id=? ORDER BY registered_at", (area_id,))

    def register_shelter(self, area_id: str, bid: str, by: str) -> None:
        self._x("INSERT OR IGNORE INTO shelters(area_id,building_id,registered_by,registered_at) VALUES (?,?,?,?)",
                (area_id, bid, by, now_iso()))

    def unregister_shelter(self, area_id: str, bid: str) -> None:
        self._x("DELETE FROM shelters WHERE area_id=? AND building_id=?", (area_id, bid))

    # ---------- survei gedung oleh petugas ----------
    def list_building_info(self, area_id: str) -> List[dict]:
        return [_binfo_out(r) for r in self._q("SELECT * FROM building_info WHERE area_id=?", (area_id,))]

    def get_building_info(self, area_id: str, bid: str) -> Optional[dict]:
        r = self._q("SELECT * FROM building_info WHERE area_id=? AND building_id=?", (area_id, bid))
        return _binfo_out(r[0]) if r else None

    def set_building_info(self, area_id: str, bid: str, f: dict, by: str) -> dict:
        self._x("""INSERT INTO building_info(area_id,building_id,levels,basement,structure,access,roof,slf,note,
                   updated_by,updated_at,open_hours,open_days,levels_confirmed,condition,ground_elev_m,
                   ground_elev_method) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(area_id,building_id) DO UPDATE SET levels=excluded.levels, basement=excluded.basement,
                   structure=excluded.structure, access=excluded.access, roof=excluded.roof, slf=excluded.slf,
                   note=excluded.note, updated_by=excluded.updated_by, updated_at=excluded.updated_at,
                   open_hours=excluded.open_hours, open_days=excluded.open_days,
                   levels_confirmed=excluded.levels_confirmed, condition=excluded.condition,
                   ground_elev_m=excluded.ground_elev_m, ground_elev_method=excluded.ground_elev_method""",
                (area_id, bid, f.get("levels"), f.get("basement", 0), f.get("structure"), f.get("access"),
                 int(bool(f.get("roof"))), int(bool(f.get("slf"))), f.get("note", ""), by, now_iso(),
                 f.get("open_hours"), f.get("open_days"), int(bool(f.get("levels_confirmed"))),
                 f.get("condition"), f.get("ground_elev_m"), f.get("ground_elev_method")))
        return self.get_building_info(area_id, bid)

    def delete_building_info(self, area_id: str, bid: str) -> None:
        self._x("DELETE FROM building_info WHERE area_id=? AND building_id=?", (area_id, bid))

    # ---------- lapisan data unggahan (diterapkan ulang setelah area dibangun ulang) ----------
    def add_layer(self, area_id: str, kind: str, source: str, name: str, data: dict, by: str) -> str:
        lid = uuid.uuid4().hex[:12]
        self._x("INSERT INTO area_layers(id,area_id,kind,source,name,data,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (lid, area_id, kind, source, name, json.dumps(data, separators=(",", ":")), by, now_iso()))
        return lid

    def list_layers(self, area_id: str) -> List[dict]:
        return self._q("SELECT * FROM area_layers WHERE area_id=? ORDER BY created_at, rowid", (area_id,))


def _binfo_out(r: dict) -> dict:
    r = dict(r)
    r["roof"] = bool(r.get("roof"))
    r["slf"] = bool(r.get("slf"))
    r["levels_confirmed"] = bool(r.get("levels_confirmed"))
    return r


def _alert_out(r: dict) -> dict:
    r = dict(r)
    r["tsunami"] = bool(r.get("tsunami"))
    r.pop("raw", None)
    return r


def _point_out(r: dict) -> dict:
    r = dict(r)
    try:
        r["services"] = json.loads(r.get("services") or "[]")
    except ValueError:
        r["services"] = []
    r["sample"] = bool(r.get("sample"))
    return r
