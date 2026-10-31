"""Aturan khusus tsunami: genangan, lantai aman, kelayakan gedung evakuasi vertikal.

Prinsip (semua konservatif; ganti dengan kajian BPBD bila tersedia):
- Perkiraan genangan di suatu titik = tinggi tsunami di pantai - elevasi tanah (model "bak mandi",
  tanpa peredaman ke arah darat sehingga cenderung melebih-lebihkan, bukan meremehkan).
- Lantai aman minimum mengikuti pedoman gedung evakuasi tsunami Kantor Kabinet Jepang (2005):
  genangan 2 m -> lantai 3 ke atas, 3 m -> lantai 4 ke atas, yaitu lantai = ceil(genangan) + 1.
  Pedoman itu juga mensyaratkan struktur beton bertulang (RC/SRC) dan standar gempa modern; hal ini
  tidak bisa dibaca dari peta, sehingga gedung berstatus "kandidat" sampai diverifikasi petugas.
- Level peringatan BMKG: Waspada (< 0,5 m), Siaga (0,5-3 m), Awas (> 3 m).
"""
from __future__ import annotations

import math
import re
import time
from typing import FrozenSet, List, NamedTuple, Optional, Sequence, Tuple

from .speed import FLOOR_HEIGHT_M, STAIR_VERTICAL_MPS

LEVELS = ("waspada", "siaga", "awas")
LEVEL_LABEL = {"waspada": "WASPADA", "siaga": "SIAGA", "awas": "AWAS"}
# Tinggi tsunami di pantai yang dipakai untuk menghitung lantai aman per level (meter).
LEVEL_HEIGHT_M = {"waspada": 0.5, "siaga": 3.0}


def wave_height_for(level: Optional[str], wave_m: Optional[float], design_m: float) -> float:
    """Tinggi tsunami yang dipakai: perkiraan resmi bila ada, lalu batas atas level, lalu skenario rencana."""
    if wave_m is not None and wave_m > 0:
        return float(wave_m)
    if level in LEVEL_HEIGHT_M:
        return LEVEL_HEIGHT_M[level]
    return float(design_m)  # Awas atau level belum diketahui -> skenario rencana (ASUMSI)


def depth_at(ground_elev_m: float, wave_m: float) -> float:
    return max(0.0, float(wave_m) - float(ground_elev_m))


def min_safe_floor(depth_m: float) -> int:
    """Lantai terendah yang aman (1 = lantai dasar)."""
    if depth_m <= 0:
        return 1
    return int(math.ceil(depth_m - 1e-9)) + 1


def floor_height_m(params: Optional[dict] = None) -> float:
    """SATU parameter tinggi per lantai (m) untuk perkiraan lantai dari tinggi DAN waktu naik tangga."""
    return float((params or {}).get("height_per_level_m") or FLOOR_HEIGHT_M)


def climb_to_floor_s(floor: int, params: Optional[dict] = None) -> float:
    return max(0, floor - 1) * floor_height_m(params) / STAIR_VERTICAL_MPS


# Struktur bangunan. "ringan" (kayu/bambu) dan "bata" (bata tanpa rangka beton) tidak memenuhi syarat
# gedung evakuasi tsunami: pedoman Jepang mensyaratkan beton bertulang/baja (RC/SRC).
STRUCTURES = ("beton", "baja", "bata", "ringan", "tidak_tahu")
WEAK_STRUCTURES = ("bata", "ringan")
ACCESS = ("24jam", "jam_kerja", "tidak")
LEVEL_SOURCES = {
    "survey": "Survei petugas",
    "osm": "OpenStreetMap (jumlah lantai)",
    "osm_height": "OpenStreetMap (tinggi gedung)",
    "google": "Google Open Buildings 2.5D (perkiraan satelit)",
    "upload": "Data tinggi gedung unggahan perencana",
    "osm_part": "OpenStreetMap (bagian gedung, building:part)",
    "default": "Tidak ada data (dianggap 1 lantai)",
}

_MATERIAL = {
    "concrete": "beton", "reinforced_concrete": "beton", "steel": "baja", "metal": "baja",
    "brick": "bata", "stone": "bata", "adobe": "bata", "cement_block": "bata",
    "wood": "ringan", "timber_framing": "ringan", "bamboo": "ringan", "palm": "ringan", "plastic": "ringan",
}


def structure_from_tags(tags: dict) -> str:
    for key in ("building:structure", "building:material"):
        v = str(tags.get(key, "")).strip().lower()
        if v in _MATERIAL:
            return _MATERIAL[v]
    return "tidak_tahu"


# Jenis gedung yang perlu survei petugas sebelum boleh dipakai (milik pribadi / hunian).
NEEDS_SURVEY_KINDS = {
    "rumah": "Rumah tinggal (bukan fasilitas umum)",
    "apartemen": "Hunian bersama (apartemen): perlu survei dan kesepakatan pengelola",
}
# Footprint minimal agar gedung berfungsi tak diketahui tanpa data lantai masuk prioritas survei. HEURISTIK:
# ±250 m² memisahkan rumah biasa dari gedung besar di permukiman Indonesia; bukan standar resmi.
SURVEY_MIN_AREA_M2 = 250.0
TIERS = ("unsafe", "unverified", "verified")
# Label yang ditampilkan ke warga/petugas/perencana (sama di semua halaman).
TIER_LABEL = {"verified": "Terverifikasi", "unverified": "Perlu Verifikasi", "unsafe": "Tidak Aman"}
# Waktu "kondisi terburuk" untuk perencanaan tanpa jam tertentu: gedung berjam buka terbatas dianggap tutup.
WORST_CASE = "terburuk"


# ---------------------------------------------------------------------------
# Zona waktu area (untuk jam buka gedung saat peringatan)
# ---------------------------------------------------------------------------
TZ_OFFSET_H = {"Asia/Jakarta": 7.0, "Asia/Pontianak": 7.0, "Asia/Makassar": 8.0, "Asia/Jayapura": 9.0}
_ZONE_LABEL = {7.0: "WIB", 8.0: "WITA", 9.0: "WIT"}
# Zona waktu per provinsi (Keppres 41/1987 dan provinsi hasil pemekaran). Zona Indonesia tidak memakai DST.
_PROV = {
    "Asia/Jakarta": ("aceh", "sumatera utara", "sumatera barat", "riau", "kepulauan riau", "jambi", "sumatera selatan",
                     "kepulauan bangka belitung", "bangka belitung", "bengkulu", "lampung", "dki jakarta",
                     "daerah khusus ibukota jakarta", "daerah khusus jakarta", "jakarta", "banten", "jawa barat",
                     "jawa tengah", "daerah istimewa yogyakarta", "yogyakarta", "jawa timur"),
    "Asia/Pontianak": ("kalimantan barat", "kalimantan tengah"),
    "Asia/Makassar": ("bali", "nusa tenggara barat", "nusa tenggara timur", "kalimantan selatan", "kalimantan timur",
                      "kalimantan utara", "sulawesi utara", "gorontalo", "sulawesi tengah", "sulawesi barat",
                      "sulawesi selatan", "sulawesi tenggara"),
    "Asia/Jayapura": ("maluku", "maluku utara", "papua", "papua barat", "papua barat daya", "papua selatan",
                      "papua tengah", "papua pegunungan"),
}
PROVINCE_TZ = {name: tz for tz, names in _PROV.items() for name in names}
_PROV_EN = {"aceh": "aceh", "north sumatra": "sumatera utara", "west sumatra": "sumatera barat",
            "south sumatra": "sumatera selatan", "riau islands": "kepulauan riau",
            "bangka belitung islands": "kepulauan bangka belitung", "jakarta special capital region": "jakarta",
            "special capital region of jakarta": "jakarta", "west java": "jawa barat", "central java": "jawa tengah",
            "east java": "jawa timur", "special region of yogyakarta": "yogyakarta",
            "west kalimantan": "kalimantan barat", "central kalimantan": "kalimantan tengah",
            "south kalimantan": "kalimantan selatan", "east kalimantan": "kalimantan timur",
            "north kalimantan": "kalimantan utara", "west nusa tenggara": "nusa tenggara barat",
            "east nusa tenggara": "nusa tenggara timur", "north sulawesi": "sulawesi utara",
            "central sulawesi": "sulawesi tengah", "west sulawesi": "sulawesi barat",
            "south sulawesi": "sulawesi selatan", "southeast sulawesi": "sulawesi tenggara",
            "north maluku": "maluku utara", "west papua": "papua barat", "southwest papua": "papua barat daya",
            "south papua": "papua selatan", "central papua": "papua tengah", "highland papua": "papua pegunungan"}


def timezone_for_province(name: Optional[str]) -> Optional[str]:
    """Nama provinsi (Nominatim address.state, Indonesia/Inggris) -> zona waktu IANA; None bila tidak dikenal."""
    n = re.sub(r"\s+", " ", str(name or "").strip().lower())
    n = re.sub(r"^(provinsi|prov\.?)\s+", "", n)
    n = re.sub(r"\s+province$", "", n)
    n = _PROV_EN.get(n, n)
    return PROVINCE_TZ.get(n)


def _utc_offsets(lon: float) -> List[float]:
    """CADANGAN bila zona waktu area tidak diketahui: kemungkinan zona dari bujur (WIB +7, WITA +8, WIT +9).

    Batas zona mengikuti provinsi, bukan bujur (mis. Kalimantan Tengah WIB hingga ±115,8° BT, Kepulauan Sula WIT
    sejak ±124° BT), jadi di sekitar batas dikembalikan DUA zona dan keduanya harus memenuhi (konservatif).
    """
    if 94.0 <= lon <= 141.5:
        offs = []
        if lon < 116.7:
            offs.append(7.0)
        if 113.5 <= lon < 127.0:
            offs.append(8.0)
        if lon >= 124.0:
            offs.append(9.0)
        return offs
    return [float(round(lon / 15.0))]


def _zone_label(off: float) -> str:
    return _ZONE_LABEL.get(float(off)) or f"UTC{off:+g}"


def tz_offsets(tz: Optional[str], ts: Optional[float] = None) -> Optional[List[float]]:
    """Offset UTC (jam) untuk zona IANA/"UTC+7"; None bila tidak dikenal."""
    if not tz:
        return None
    if tz in TZ_OFFSET_H:
        return [TZ_OFFSET_H[tz]]
    m = re.fullmatch(r"(?:UTC|GMT)\s*([+-]\d{1,2}(?:[.:]\d{2})?)", str(tz).strip(), re.I)
    if m:
        hh, _, mm = m.group(1).replace(".", ":").partition(":")
        h = float(hh)
        return [h + (math.copysign(int(mm) / 60.0, h) if mm else 0.0)]
    try:  # zona lain (luar Indonesia): pakai basis data zona waktu bila tersedia
        import datetime as _dt
        from zoneinfo import ZoneInfo
        d = _dt.datetime.fromtimestamp(ts if ts is not None else time.time(), _dt.timezone.utc)
        return [ZoneInfo(str(tz)).utcoffset(d).total_seconds() / 3600.0]
    except Exception:
        return None


def area_timezone(meta: dict) -> dict:
    """Zona waktu area: eksplisit (meta.timezone dari prepare_area/perencana/provinsi) atau, bila tidak ada, bujur.

    src: "eksplisit" | "provinsi" | "perencana" | "bujur" (cadangan; di dekat batas zona dua zona dicek sekaligus).
    """
    tz = meta.get("timezone")
    offs = tz_offsets(tz)
    if offs:
        return {"tz": tz, "offsets": offs, "label": _zone_label(offs[0]), "src": meta.get("timezone_src") or "eksplisit"}
    offs = _utc_offsets(float(meta["center"][0]))
    return {"tz": None, "offsets": offs, "label": "/".join(_zone_label(o) for o in offs), "src": "bujur"}


class AlertTime(NamedTuple):
    """Waktu yang dipakai untuk akses gedung: ts = jam peringatan (unix), offsets = zona setempat yang mungkin,
    window_s = gedung harus terbuka sepanjang [ts, ts + window_s] (perkiraan waktu sampai gelombang tiba)."""
    ts: float
    offsets: Tuple[float, ...]
    window_s: float = 0.0
    label: str = ""
    src: str = ""


def alert_time(ts: float, tzinfo: dict, window_s: float = 0.0) -> AlertTime:
    return AlertTime(float(ts), tuple(tzinfo["offsets"]), max(0.0, float(window_s or 0.0)), tzinfo.get("label", ""),
                     tzinfo.get("src", ""))


def _coerce_at(at):
    """None (tanpa jam), WORST_CASE, AlertTime, atau format lama (unix_ts, bujur)."""
    if at is None or at == WORST_CASE or isinstance(at, AlertTime):
        return at
    if isinstance(at, (tuple, list)) and len(at) >= 2 and at[0] is not None:
        return AlertTime(float(at[0]), tuple(_utc_offsets(float(at[1]))), 0.0, "", "bujur")
    return None


def local_time_label(at, lon: Optional[float] = None) -> str:
    """'21.05 WIB'. Menerima AlertTime, atau (unix_ts, bujur) seperti versi lama."""
    a = _coerce_at((at, lon) if lon is not None else at)
    if not isinstance(a, AlertTime):
        return "-"
    off = a.offsets[0]
    m = int(((a.ts / 60.0) + off * 60) % 1440)
    return f"{m // 60:02d}.{m % 60:02d} {_zone_label(off)}"


# ---------------------------------------------------------------------------
# Jam buka gedung (dari survei petugas)
# ---------------------------------------------------------------------------
_HOURS_RE = re.compile(r"^\s*([01]?\d|2[0-4])[.:]([0-5]\d)\s*-\s*([01]?\d|2[0-4])[.:]([0-5]\d)\s*$")
DAY_NAMES = ("Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min")


def parse_open_hours(s) -> Optional[List[Tuple[int, int]]]:
    """'07:00-16:00', beberapa rentang ('04:30-06:00, 11:30-20:00'), atau melewati tengah malam ('18:00-06:00').

    Hasil: [(menit_buka, menit_tutup)]; None bila kosong/tidak valid (TIDAK pernah ditebak).
    """
    if not s:
        return None
    out = []
    for part in str(s).split(","):
        m = _HOURS_RE.match(part)
        if not m:
            return None
        a, b = int(m.group(1)) * 60 + int(m.group(2)), int(m.group(3)) * 60 + int(m.group(4))
        if a >= 1440 or b > 1440 or a == b:
            return None
        out.append((a, b))
    return out or None


def parse_open_days(s) -> FrozenSet[int]:
    """'01234' = Senin–Jumat (Senin = 0). Kosong = setiap hari."""
    d = {int(c) for c in str(s or "") if c in "0123456"}
    return frozenset(d) if d else frozenset(range(7))


def hours_label(b: dict) -> str:
    hours = parse_open_hours(b.get("open_hours"))
    if not hours:
        return "jam buka belum dicatat"
    txt = ", ".join(f"{a // 60:02d}.{a % 60:02d}–{c // 60:02d}.{c % 60:02d}" for a, c in hours)
    days = parse_open_days(b.get("open_days"))
    if len(days) < 7:
        txt += " (" + ", ".join(DAY_NAMES[d] for d in sorted(days)) + ")"
    return txt


def _open_at(hours, days, local_s: float) -> bool:
    day = int(local_s // 86400)
    mins = (local_s % 86400) / 60.0
    wd = (day + 3) % 7  # 1 Jan 1970 = Kamis (Senin = 0)
    for a, b in hours:
        if a < b:
            if wd in days and a <= mins < b:
                return True
        else:  # melewati tengah malam: buka hari wd pukul a, tutup keesokan harinya pukul b
            if wd in days and mins >= a:
                return True
            if (wd - 1) % 7 in days and mins < b:
                return True
    return False


def _always_open(hours, days) -> bool:
    if len(days) < 7:
        return False
    cover = [False] * 1440
    for a, b in hours:
        for m in (range(a, b) if a < b else list(range(a, 1440)) + list(range(0, b))):
            cover[m] = True
    return all(cover)


def _open_through(hours, days, at: AlertTime) -> bool:
    """Terbuka sepanjang [ts, ts + window] untuk SEMUA zona yang mungkin (diperiksa tiap menit, rentang menit)."""
    steps = int(math.ceil(at.window_s / 60.0))
    for off in at.offsets:
        base = at.ts + off * 3600.0
        for k in range(steps + 1):
            if not _open_at(hours, days, base + min(k * 60.0, at.window_s)):
                return False
    return True


def access_state(b: dict, at=None) -> Tuple[str, str]:
    """Akses ke lantai atas: ("open" | "closed" | "unknown" | "limited", keterangan).

    - "24jam" (survei) -> open; "tidak" -> closed.
    - Belum diketahui -> unknown: TIDAK dianggap terbuka (paling tinggi "Perlu Verifikasi"), tapi tidak dibuang.
    - "jam_kerja" -> jam buka HASIL SURVEI (bukan jam tetap): open/closed menurut waktu peringatan; jam buka belum
      dicatat -> closed bila ada waktu (lebih aman daripada mengarahkan warga ke gedung yang mungkin terkunci).
    - at None = tanpa waktu (tampilan) -> limited; WORST_CASE (perencanaan) -> closed kecuali buka 24 jam.
    """
    acc = b.get("access")
    if acc == "tidak":
        return "closed", "Lantai atas tidak dapat diakses umum"
    if acc == "24jam":
        return "open", ""
    if acc != "jam_kerja":
        return "unknown", "Akses masuk belum dipastikan"
    at = _coerce_at(at)
    hours = parse_open_hours(b.get("open_hours"))
    if hours is None:
        if at is None:
            return "limited", "Akses terbatas; jam buka belum dicatat petugas"
        return "closed", "Akses terbatas dan jam buka belum dicatat petugas — dianggap terkunci"
    days = parse_open_days(b.get("open_days"))
    lbl = hours_label(b)
    if at is None:
        return "limited", f"Hanya bisa dimasuki pukul {lbl}"
    if at == WORST_CASE:
        if _always_open(hours, days):
            return "open", ""
        return "closed", f"Hanya bisa dimasuki pukul {lbl}; perencanaan memakai kondisi terburuk (tsunami bisa kapan saja)"
    if _open_through(hours, days, at):
        return "open", f"Terbuka saat peringatan (pukul {lbl})"
    return "closed", f"Tutup saat peringatan (pukul {local_time_label(at)}; jam buka {lbl}) — gedung mungkin terkunci"


def next_access_change(buildings: Sequence[dict], at) -> Optional[float]:
    """Waktu (unix) terdekat setelah at.ts saat status buka/tutup suatu gedung bisa berubah; None bila tidak ada.

    Dipakai server untuk menghitung ulang tabel rute dan oleh HP (mode offline) untuk tahu kapan tabel kedaluwarsa.
    """
    at = _coerce_at(at)
    if not isinstance(at, AlertTime):
        return None
    w = int(math.ceil(at.window_s / 60.0))
    best = None
    for b in buildings:
        if b.get("access") != "jam_kerja":
            continue
        hours = parse_open_hours(b.get("open_hours"))
        if not hours:
            continue
        mins = {0} | {a for a, _ in hours} | {(c - w) % 1440 for _, c in hours}
        for off in at.offsets:
            local = at.ts + off * 3600.0
            day0 = math.floor(local / 86400.0) * 86400.0
            for m in mins:
                for d in (0, 1):
                    t = day0 + d * 86400.0 + m * 60.0 - off * 3600.0
                    if t > at.ts + 1:
                        best = t if best is None else min(best, t)
                        break
    return best


# ---------------------------------------------------------------------------
# Kelayakan (SATU sumber kebenaran: eligible -> verification -> assess)
# ---------------------------------------------------------------------------
# Kategori OSM `building=*` yang TIDAK PERNAH boleh menjadi tempat evakuasi vertikal (audit D-1): bangunan belum jadi,
# reruntuhan, atap/naungan tanpa lantai, parkiran, bangunan kecil/sementara. Kebijakan proyek: konservatif.
UNSAFE_BUILDING = {
    "construction": "Bangunan sedang dibangun (building=construction)",
    "ruins": "Reruntuhan (building=ruins)",
    "roof": "Hanya atap/naungan tanpa lantai (building=roof)",
    "parking": "Gedung parkir (building=parking) — dikecualikan kebijakan proyek",
    "carport": "Naungan kendaraan (building=carport)",
    "garage": "Garasi (building=garage)", "garages": "Garasi (building=garages)",
    "shed": "Gudang kecil (building=shed)", "kiosk": "Kios (building=kiosk)",
    "container": "Kontainer (building=container)", "greenhouse": "Rumah kaca (building=greenhouse)",
    "toilets": "Toilet umum (building=toilets)", "transformer_tower": "Gardu listrik (building=transformer_tower)",
    "demolished": "Sudah dibongkar (building=demolished)", "collapsed": "Runtuh (building=collapsed)",
    "damaged": "Rusak (building=damaged)", "abandoned": "Terbengkalai (building=abandoned)",
}
# Tag siklus hidup OSM: yes/true pada salah satu kunci ini, atau kunci berawalan "<lifecycle>:" (mis. abandoned:building).
LIFECYCLE_KEYS = ("abandoned", "disused", "demolished", "razed", "destroyed", "ruins", "construction", "proposed",
                  "removed", "was")
BAD_CONDITION = {"ruined": "Kondisi hancur (building:condition=ruined)",
                 "poor": "Kondisi buruk (building:condition=poor)",
                 "deteriorating": "Kondisi memburuk (building:condition=deteriorating)",
                 "demolished": "Sudah dibongkar (building:condition=demolished)"}
# Kondisi hasil survei petugas. Hanya "baik" yang bisa terverifikasi; tiga terakhir = Tidak Aman.
CONDITIONS = ("baik", "rusak_ringan", "rusak_berat", "dalam_pembangunan", "terbengkalai")
CONDITION_LABEL = {"baik": "Baik", "rusak_ringan": "Rusak ringan", "rusak_berat": "Rusak berat",
                   "dalam_pembangunan": "Dalam pembangunan", "terbengkalai": "Terbengkalai"}
UNSAFE_CONDITIONS = ("rusak_berat", "dalam_pembangunan", "terbengkalai")
# Cara petugas mengukur elevasi tanah gedung. Hanya ini yang memberi kepercayaan elevasi "tinggi" (margin 0 m).
ELEV_METHODS = {"gnss_geodetik": "GNSS geodetik (RTK/statik)", "benchmark": "Diikat ke titik tinggi (BM)",
                "survei_topografi": "Survei topografi (total station/waterpass)"}
# Margin galat elevasi (m) per tingkat kepercayaan: genangan konservatif = H - (elev - margin). KEPUTUSAN PROYEK
# 1 Nov 2026, parameter awal yang perlu divalidasi BPBD/data elevasi lebih akurat. "tidak_ada": elevasi dihitung 0 m.
DEFAULT_ELEV_MARGIN_M = {"tinggi": 0.0, "sedang": 3.0, "rendah": 5.0, "tidak_ada": 0.0}
ELEV_CONF_LABEL = {"tinggi": "tinggi (diukur petugas)", "sedang": "sedang", "rendah": "rendah",
                   "tidak_ada": "tidak tersedia"}


def osm_unsafe_reason(tags: dict) -> str:
    """Alasan Tidak Aman dari tag OSM ('' bila tidak ada). Dipanggil saat area dibangun; hasilnya disimpan di gedung."""
    bv = str(tags.get("building", "")).strip().lower()
    if bv in UNSAFE_BUILDING:
        return UNSAFE_BUILDING[bv]
    for k in LIFECYCLE_KEYS:
        v = str(tags.get(k, "")).strip().lower()
        if v in ("yes", "true", "1") or (k != "was" and v and k in ("abandoned", "disused", "ruins") and v != "no"):
            return f"Tag OSM {k}={tags.get(k)}"
    for key in tags:
        pre = str(key).split(":", 1)[0]
        if ":" in str(key) and pre in LIFECYCLE_KEYS and pre != "was":
            return f"Tag OSM siklus hidup {key}={tags.get(key)}"
    cond = str(tags.get("building:condition", "")).strip().lower()
    if cond in BAD_CONDITION:
        return BAD_CONDITION[cond]
    return ""


def _surveyed(b: dict) -> bool:
    """Sudah disurvei petugas (BUKAN berarti terverifikasi: lihat `verification`). `verified` = kunci lama."""
    return bool(b.get("surveyed") or b.get("verified")) or b.get("levels_src") == "survey"


def _elev(b: dict) -> Optional[float]:
    v = b.get("elev")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def elev_confidence(b: dict) -> str:
    """"tinggi" (diukur petugas), "sedang" (DEM di gedung cocok dengan jalan dekat), "rendah", "tidak_ada".

    DEM global tidak pernah "tinggi": tingkat itu hanya dari pengukuran lapangan (survei, ELEV_METHODS).
    """
    if _elev(b) is None:
        return "tidak_ada"
    if b.get("elev_src") == "survei" and b.get("elev_method") in ELEV_METHODS:
        return "tinggi"
    c = b.get("elev_conf")
    if c in ("sedang", "rendah"):
        return c
    return "rendah" if b.get("elev_uncertain") else "sedang"


def elev_margin(conf: str, params: Optional[dict] = None) -> float:
    m = dict(DEFAULT_ELEV_MARGIN_M)
    m.update((params or {}).get("elev_margin_m") or {})
    return float(m.get(conf, DEFAULT_ELEV_MARGIN_M["rendah"]))


def eligible(b: dict) -> Tuple[bool, str]:
    """Apakah bangunan BOLEH dipertimbangkan sebagai tempat evakuasi vertikal (sebelum melihat tinggi air).

    False = TIDAK AMAN. True belum berarti terverifikasi: lihat `verification`.
    """
    if b.get("osm_unsafe"):
        return False, b["osm_unsafe"]
    if b.get("condition") in UNSAFE_CONDITIONS:
        return False, f"Kondisi bangunan hasil survei: {CONDITION_LABEL[b['condition']]}"
    if b.get("levels_src", "default") == "default":
        return False, "Jumlah lantai tidak diketahui"
    if b.get("kind") in NEEDS_SURVEY_KINDS and not _surveyed(b):
        return False, NEEDS_SURVEY_KINDS[b["kind"]]
    if b.get("structure") in WEAK_STRUCTURES:
        return False, "Struktur bukan beton/baja"
    if b.get("access") == "tidak":
        return False, "Lantai atas tidak dapat diakses umum"
    return True, ""


def conflict_text(b: dict) -> str:
    c = b.get("levels_conflict") or {}
    if not c:
        return ""
    return "Data jumlah lantai antar-sumber berbeda (" + ", ".join(
        f"{LEVEL_SOURCES.get(k, k).split(' (')[0]}: {v}" for k, v in sorted(c.items())) + ")"


def caveats(b: dict) -> List[str]:
    """Hal yang belum pasti pada gedung yang lolos `eligible` (ditampilkan ke warga & perencana)."""
    out = []
    surveyed = _surveyed(b)
    if not surveyed:
        out.append("Belum disurvei petugas")
    if b.get("kind") == "tidak_diketahui":
        out.append("Fungsi gedung tidak diketahui")
    if b.get("osm_unsafe") is None and b.get("kind") in ("lainnya", "tidak_diketahui") and not surveyed:
        out.append("Tag kondisi OSM tidak tersimpan (data area lama)")
    if b.get("levels_est"):
        out.append("Jumlah lantai perkiraan dari tinggi gedung")
    elif surveyed and b.get("levels_src") != "survey":
        out.append("Jumlah lantai belum dihitung langsung oleh petugas")
    if b.get("levels_conflict") and b.get("levels_src") != "survey":
        out.append(conflict_text(b))
    if b.get("structure") not in ("beton", "baja"):
        out.append("Struktur belum diperiksa")
    if surveyed and b.get("condition") != "baik":
        out.append("Kondisi bangunan belum dicatat baik" if not b.get("condition")
                   else f"Kondisi bangunan: {CONDITION_LABEL.get(b['condition'], b['condition'])}")
    st, why = access_state(b, None)
    if st in ("unknown", "limited"):
        out.append(why)
    conf = elev_confidence(b)
    if conf == "tidak_ada":
        out.append("Elevasi tanah tidak tersedia (dihitung 0 m)")
    elif conf == "rendah":
        out.append("Elevasi tanah kurang pasti")
    return out


def access_known(b: dict) -> bool:
    """Akses tercatat lengkap: 24 jam, atau jam tertentu DENGAN jam buka hasil survei. Buka/tutup gedung berjam
    terbatas tetap dinilai per skenario (`access_state`: jam peringatan s.d. gelombang tiba; perencanaan = terburuk)."""
    return b.get("access") == "24jam" or (b.get("access") == "jam_kerja" and bool(parse_open_hours(b.get("open_hours"))))


def verification(b: dict) -> Tuple[str, List[str]]:
    """Tingkat kepercayaan gedung sebagai tempat evakuasi vertikal (tidak bergantung skenario).

    unsafe     : tidak boleh dipakai (lihat `eligible`) — tidak pernah jadi tujuan, walau terdaftar.
    unverified : "Perlu Verifikasi" — daftar verifikasi BPBD; BUKAN tujuan rute/alokasi (hanya info tanpa rute).
    verified   : disurvei petugas: kondisi baik, struktur beton/baja, akses tercatat (24 jam, atau jam tertentu dengan
                 jam buka; buka/tutup dinilai per skenario), jumlah lantai dihitung langsung
                 (bukan perkiraan), dan elevasi tanah tersedia. Struktur/akses/kondisi yang tidak diketahui TIDAK
                 pernah dianggap aman.
    Lantai aman per skenario (margin DEM) dinilai di `assess`, bukan di sini.
    """
    ok, why = eligible(b)
    if not ok:
        return "unsafe", [why]
    cv = caveats(b)
    if (_surveyed(b) and b.get("levels_src") == "survey" and not b.get("levels_est")
            and b.get("structure") in ("beton", "baja") and access_known(b)
            and b.get("condition") == "baik" and elev_confidence(b) != "tidak_ada"):
        return "verified", [c for c in cv if c.startswith("Elevasi")]
    return "unverified", cv


def shelter_capacity(area_m2: float, usable_floors: int, params: dict) -> int:
    """SATU-SATUNYA rumus kapasitas: luas lantai x lantai terpakai x faktor guna / ruang per orang (FEMA P-646).

    Luas tidak diketahui -> 0 (tidak pernah dianggap tak terbatas).
    """
    if usable_floors <= 0 or not area_m2 or area_m2 <= 0:
        return 0
    factor = float(params.get("shelter_usable_factor", 0.5))
    per_person = float(params.get("space_per_person_m2", 0.93))
    return int(area_m2 * usable_floors * factor / per_person)


def assess(b: dict, wave_m: float, params: dict, at=None) -> dict:
    """Kelayakan satu gedung sebagai tempat evakuasi vertikal untuk tinggi tsunami tertentu.

    Lantai aman dihitung DUA kali: nominal (genangan = H - elev) dan KONSERVATIF (genangan = H - (elev - margin),
    margin per kepercayaan elevasi, `elev_margin`). Semua keputusan (suitable, kapasitas, waktu naik, instruksi
    "naik minimal ke lantai N") memakai yang konservatif (`min_floor`); nominal hanya untuk ditampilkan.
    Elevasi tidak tersedia dihitung 0 m. `destination` = boleh menjadi tujuan rute & alokasi = suitable DAN verified.
    """
    levels = int(b.get("levels") or 1)
    elev = _elev(b)
    e0 = elev if elev is not None else 0.0
    conf = elev_confidence(b)
    margin = elev_margin(conf, params)
    depth_nom = depth_at(e0, wave_m)
    floor_nom = min_safe_floor(depth_nom)
    depth = depth_at(e0 - margin, wave_m)
    floor = min_safe_floor(depth)
    # Lantai yang bisa menampung: dari lantai aman (konservatif) ke atas, dan tidak di bawah building:min_level
    # (bagian gedung tertinggi yang menggantung tidak punya luas di lantai bawahnya).
    first = max(floor if depth > 0 else 1, int(b.get("min_level") or 0) + 1)
    usable = max(0, levels - first + 1)
    capacity = shelter_capacity(float(b.get("cap_area_m2") or b.get("area_m2") or 0.0), usable, params)
    ok, why = eligible(b)
    tier, cv = verification(b)
    acc, acc_why = access_state(b, at)
    closed = bool(ok and acc == "closed")
    if ok and depth <= 0:
        ok, why = False, ("Tanah lebih tinggi dari perkiraan tsunami ini"
                          + (f" (termasuk margin galat elevasi {margin:g} m)" if margin else "")
                          + " — tidak perlu naik gedung")
    elif ok and levels < floor:
        nom = f"; tanpa margin: lantai {floor_nom}" if floor_nom != floor else ""
        ok, why = False, (f"Terlalu rendah: perlu lantai {floor} ke atas (konservatif, margin elevasi {margin:g} m"
                          f"{nom}), gedung {levels} lantai")
    elif ok and capacity <= 0:
        ok, why = False, "Kapasitas tidak diketahui (luas lantai tidak tersedia)"
    elif ok and closed:
        ok, why = False, acc_why
    return {"depth_m": round(depth, 1), "min_floor": floor, "usable_floors": usable,
            "depth_nominal_m": round(depth_nom, 1), "min_floor_nominal": floor_nom, "elev_margin_m": margin,
            "capacity": capacity, "climb_s": round(climb_to_floor_s(floor, params), 1),
            "suitable": ok, "reason": why, "tier": tier, "tier_label": TIER_LABEL[tier], "caveats": cv,
            "destination": bool(ok and tier == "verified"),
            "closed_now": closed, "access_state": acc, "elev_conf": conf}


def registered_status(b: dict, a: dict) -> Tuple[bool, str]:
    """Apakah shelter yang DIDAFTARKAN perencana dipakai sebagai shelter resmi pada skenario ini, dan alasannya bila tidak.

    Syarat: tidak "unsafe", tier "verified", lantai cukup tinggi menurut lantai aman KONSERVATIF, kapasitas > 0, dan
    terbuka pada waktu peringatan. Status terdaftar TIDAK pernah meloloskan gedung yang tidak memenuhi syarat. Bedanya
    dengan `assess(...)["suitable"]`: shelter resmi tetap dipakai walau tanahnya di atas genangan konservatif.
    """
    ok, why = eligible(b)
    if not ok:
        return False, why
    if a["tier"] != "verified":
        return False, "Perlu Verifikasi: " + "; ".join(a["caveats"] or ["survei petugas belum lengkap"])
    if b.get("access") != "24jam":
        return False, "Shelter resmi wajib berakses 24 jam (gedung ini hanya buka pukul " + hours_label(b) + ")"
    if int(b.get("levels") or 1) < a["min_floor"]:
        return False, (f"Terlalu rendah: perlu lantai {a['min_floor']} ke atas (konservatif), "
                       f"gedung {int(b.get('levels') or 1)} lantai")
    if a["capacity"] <= 0:
        return False, "Kapasitas tidak diketahui (luas lantai tidak tersedia)"
    if a.get("closed_now"):
        return False, a["reason"] or "Tutup pada waktu peringatan"
    return True, ""


def registered_ok(b: dict, a: dict) -> bool:
    return registered_status(b, a)[0]


def can_register(b: dict) -> Tuple[bool, str]:
    """Syarat mendaftarkan shelter resmi (tidak bergantung skenario): tidak unsafe dan terverifikasi."""
    tier, cv = verification(b)
    if tier == "unsafe":
        return False, f"Tidak Aman: {cv[0]}"
    if tier == "verified" and b.get("access") != "24jam":
        # Shelter RESMI (papan/rambu publik) wajib bisa dimasuki 24 jam; gedung berjam terbatas yang terverifikasi
        # hanya menjadi tujuan gedung saat buka (lihat assess/access_state).
        return False, ("Shelter resmi wajib disurvei dengan akses 24 jam. Gedung ini hanya bisa dimasuki pukul "
                       + hours_label(b) + "; tetap dipakai sebagai tujuan evakuasi vertikal saat buka")
    if tier != "verified":
        return False, ("Perlu Verifikasi — shelter resmi wajib disurvei petugas lebih dulu (kondisi baik, struktur "
                       "beton/baja, akses & jam buka tercatat, jumlah lantai dihitung langsung, elevasi tersedia). Belum terpenuhi: "
                       + "; ".join(cv or ["survei petugas belum lengkap"]))
    return True, ""


def survey_priority(b: dict, params: dict) -> bool:
    """Gedung yang layak diprioritaskan untuk disurvei petugas: lolos `eligible` atau hanya kurang data (lantai/
    akses/elevasi), bukan Tidak Aman permanen (kategori OSM, kondisi survei, struktur lemah, rumah)."""
    if b.get("osm_unsafe") or b.get("condition") in UNSAFE_CONDITIONS:
        return False
    if b.get("kind") == "rumah" or b.get("structure") in WEAK_STRUCTURES:
        return False
    tier, _ = verification(b)
    if tier == "verified":
        return elev_confidence(b) != "tinggi" and b.get("hazard") == 1  # sudah layak; pengukuran elevasi masih membantu
    if b.get("access") == "jam_kerja" and not parse_open_hours(b.get("open_hours")):
        return True
    if b.get("levels_conflict") and b.get("levels_src") != "survey":
        return True
    if int(b.get("levels") or 1) >= int(params.get("shelter_min_levels", 2)):
        return True
    if b.get("levels_src", "default") == "default":
        return (b.get("kind") not in ("tidak_diketahui", "lainnya")
                or float(b.get("area_m2") or 0) >= SURVEY_MIN_AREA_M2)
    return False


def refresh_flags(b: dict, params: dict) -> None:
    """Hitung ulang penanda turunan gedung (SATU tempat). `tier` = tingkat kepercayaan (tanpa skenario) yang dipakai
    peta, legend, dan detail di ketiga peran. Kapasitas TIDAK disimpan: selalu dari `assess`."""
    tier, cv = verification(b)
    b["tier"] = tier
    b["tier_reasons"] = cv[:4]
    b["survey_prio"] = survey_priority(b, params)
    for k in ("candidate", "capacity", "climb_s"):
        b.pop(k, None)
