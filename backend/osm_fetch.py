"""Pengambilan data nyata dari internet: OpenStreetMap (Overpass), elevasi (DEM), nama tempat.

Semua sumber gratis dan tanpa kunci API:
- Overpass API      : jalan, garis pantai, sungai, fasilitas (© kontributor OpenStreetMap, ODbL)
- AWS Terrain Tiles : elevasi global dari SRTM dkk. (format Terrarium PNG), ± 19 m/piksel di zoom 13
- Open-Meteo        : cadangan elevasi (Copernicus DEM GLO-90)
- Nominatim         : nama kelurahan/kota dari koordinat
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import __version__
from .pngdec import decode as png_decode

UA = f"SEMPAT/{__version__} (aplikasi evakuasi tsunami, hackathon INCEPTION 2026)"
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
TERRARIUM_URLS = [
    "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png",
    "https://elevation-tiles-prod.s3.amazonaws.com/terrarium/{z}/{x}/{y}.png",
]
Log = Callable[[str], None]


def _get(url: str, timeout: float = 30, data: Optional[bytes] = None) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class OverpassQueryError(RuntimeError):
    """Server Overpass menolak query (HTTP 400): percobaan ulang tidak akan membantu."""


# ---------------------------------------------------------------------------
# Overpass
# ---------------------------------------------------------------------------
FACILITY_AMENITY = "hospital|clinic|doctors|police|fire_station|townhall|community_centre"
# Gedung yang dimuat peta otomatis warga/petugas walau belum punya tag jumlah lantai: fungsi umum/komersial yang
# sering bertingkat, atau footprint besar. Aturan yang sama dipakai query Overpass dan mode uji (relevant_building).
PUBLIC_BUILDING_RX = ("school|college|university|kindergarten|hospital|clinic|government|public|civic|townhall|"
                      "fire_station|mosque|church|temple|chapel|cathedral|religious|hotel|commercial|retail|office|"
                      "supermarket|apartments|dormitory")
PUBLIC_AMENITY_RX = ("school|college|university|kindergarten|hospital|clinic|doctors|townhall|police|fire_station|"
                     "courthouse|place_of_worship|community_centre")
SMALL_BUILDING_RX = ("house|detached|semidetached_house|terrace|hut|bungalow|cabin|garage|garages|shed|roof|carport|"
                     "farm_auxiliary|greenhouse|kiosk")
# Keliling > 80 m ≈ footprint > 400 m² bila persegi: gedung besar tanpa tag lain. HEURISTIK, bukan standar.
BIG_FOOTPRINT_PERIMETER_M = 80
TALL_LEVELS_RX = "^([2-9]|[1-9][0-9]+)"


def relevant_building(tags: dict, geometry: Optional[Sequence[dict]] = None) -> bool:
    """Padanan Python dari filter gedung peta otomatis (overpass_query(buildings="tall"))."""
    bv = str(tags.get("building", ""))
    if not bv or bv == "no":
        return False
    if re.match(TALL_LEVELS_RX, str(tags.get("building:levels", ""))) or tags.get("height"):
        return True
    if re.fullmatch(f"({PUBLIC_BUILDING_RX})", bv) or re.fullmatch(f"({PUBLIC_AMENITY_RX})", tags.get("amenity", "")):
        return True
    if tags.get("tourism") in ("hotel", "guest_house", "hostel", "motel") or tags.get("office"):
        return True
    if geometry and not re.fullmatch(f"({SMALL_BUILDING_RX})", bv):
        pts = [(g["lat"], g["lon"]) for g in geometry if g]
        per = sum(_hav(a, b) for a, b in zip(pts[:-1], pts[1:]))
        return per > BIG_FOOTPRINT_PERIMETER_M
    return False


def _hav(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(h)))


def overpass_query(bbox: Tuple[float, float, float, float], *, coast_pad_deg: float = 0.035,
                   buildings=False, facilities: bool = True, timeout: int = 90) -> str:
    """bbox = (selatan, barat, utara, timur).

    buildings:
    - True: semua bangunan (way + relation multipolygon + building:part) — analisis perencana.
    - "tall": gedung relevan untuk peta otomatis warga/petugas (lihat relevant_building) + relation multipolygon +
      building:part bertingkat beserta garis luarnya. Rumah kecil tidak diunduh agar peta tetap ringan.
    - "tall_legacy": filter lama (hanya gedung bertag jumlah lantai >= 2 atau height); cadangan bila server menolak.
    - False: tanpa bangunan.
    """
    s, w, n, e = bbox
    b = f"{s:.6f},{w:.6f},{n:.6f},{e:.6f}"
    cb = f"{s - coast_pad_deg:.6f},{w - coast_pad_deg:.6f},{n + coast_pad_deg:.6f},{e + coast_pad_deg:.6f}"
    q = [f"[out:json][timeout:{timeout}];"]
    if buildings == "tall":
        q += ["(", f'  way["building:part"]["building:levels"]({b});', f'  way["building:part"]["height"]({b});',
              ")->.parts;"]
    q += ["(",
          f'  way["highway"]({b});',
          f'  way["natural"="coastline"]({cb});',
          f'  way["waterway"~"^(river|canal)$"]({b});']
    if buildings in ("tall", "tall_legacy"):
        q.append(f'  way["building"]["building:levels"~"{TALL_LEVELS_RX}"]({b});')
        q.append(f'  way["building"]["height"]({b});')
    if buildings == "tall":
        q += [f'  way["building"~"^({PUBLIC_BUILDING_RX})$"]({b});',
              f'  way["building"]["amenity"~"^({PUBLIC_AMENITY_RX})$"]({b});',
              f'  way["building"]["tourism"~"^(hotel|guest_house|hostel|motel)$"]({b});',
              f'  way["building"]["office"]({b});',
              f'  way["building"]["building"!~"^({SMALL_BUILDING_RX})$"](if: length() > {BIG_FOOTPRINT_PERIMETER_M})'
              f'({b});',
              f'  relation["building"]["type"="multipolygon"]({b});',
              '  .parts;',
              '  way(around.parts:1)["building"];']
    elif buildings is True:
        q += [f'  way["building"]({b});',
              f'  relation["building"]["type"="multipolygon"]({b});',
              f'  way["building:part"]({b});']
    q += [")->.net;", ".net out geom;"]
    if facilities:
        q += ["(",
              f'  nwr["amenity"~"^({FACILITY_AMENITY})$"]({b});',
              f'  nwr["emergency"="assembly_point"]({b});',
              f'  nwr["healthcare"~"^(hospital|clinic|centre)$"]({b});',
              ")->.fac;", ".fac out center;"]
    return "\n".join(q)


def fetch_overpass(query: str, log: Log = print, timeout: float = 120) -> dict:
    last: Optional[Exception] = None
    for url in OVERPASS_URLS:
        for attempt in range(2):
            try:
                log(f"  Overpass: {url.split('/')[2]} (percobaan {attempt + 1})")
                body = urllib.parse.urlencode({"data": query}).encode()
                return json.loads(_get(url, timeout=timeout, data=body).decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 400:  # query ditolak (sintaks/fitur tak didukung): jangan ulangi ke server lain
                    raise OverpassQueryError(f"Query Overpass ditolak: {exc}") from exc
                last = exc
                log(f"    gagal: {exc}")
                time.sleep(2 + 3 * attempt)
            except Exception as exc:  # server sibuk (429/504), jaringan, dsb.
                last = exc
                log(f"    gagal: {exc}")
                time.sleep(2 + 3 * attempt)
    raise RuntimeError(f"Server peta OpenStreetMap (Overpass) tidak bisa dihubungi: {last}")


FAC_KIND = {
    "hospital": "faskes", "clinic": "faskes", "doctors": "faskes", "centre": "faskes",
    "police": "polisi", "fire_station": "damkar", "townhall": "kantor_pemerintah",
    "community_centre": "balai_warga",
}
FAC_LABEL = {
    "faskes": "Fasilitas kesehatan", "polisi": "Kantor polisi", "damkar": "Pemadam kebakaran",
    "kantor_pemerintah": "Kantor pemerintahan", "balai_warga": "Balai warga",
    "titik_kumpul_osm": "Titik kumpul (OSM)",
}


def extract_facilities(overpass: dict) -> List[dict]:
    """Fasilitas publik dari OSM sebagai cadangan tujuan setelah warga aman (belum diverifikasi petugas)."""
    out: List[dict] = []
    seen = set()
    for el in overpass.get("elements", []):
        tags = el.get("tags") or {}
        kind = None
        if tags.get("emergency") == "assembly_point":
            kind = "titik_kumpul_osm"
        elif tags.get("amenity") in FAC_KIND:
            kind = FAC_KIND[tags["amenity"]]
        elif tags.get("healthcare") in FAC_KIND:
            kind = FAC_KIND[tags["healthcare"]]
        if not kind:
            continue
        if "lat" in el and "lon" in el:
            lat, lon = el["lat"], el["lon"]
        elif el.get("center"):
            lat, lon = el["center"]["lat"], el["center"]["lon"]
        else:
            continue
        name = tags.get("name") or tags.get("name:id")
        if not name:
            if kind != "titik_kumpul_osm":
                continue
            name = "Titik kumpul"
        key = (name, round(lat, 4), round(lon, 4))
        if key in seen:
            continue
        seen.add(key)
        out.append({"id": f"osm-{el.get('type', 'n')[0]}{el.get('id')}", "name": name, "kind": kind,
                    "lat": round(lat, 7), "lon": round(lon, 7),
                    "tsunami": tags.get("assembly_point:tsunami") == "yes"})
    return out


# ---------------------------------------------------------------------------
# Elevasi
# ---------------------------------------------------------------------------
def _tile_xy(lat: float, lon: float, z: int) -> Tuple[float, float]:
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    la = math.radians(max(-85.0, min(85.0, lat)))
    y = (1.0 - math.log(math.tan(la) + 1.0 / math.cos(la)) / math.pi) / 2.0 * n
    return x, y


class TerrariumDEM:
    """Elevasi dari AWS Terrain Tiles (Terrarium): tinggi = R*256 + G + B/256 - 32768 meter."""

    name = "Elevasi: AWS Terrain Tiles (SRTM dkk.)"

    def __init__(self, cache_dir: Optional[Path] = None, z: int = 13, log: Log = print,
                 fetch: Callable[[str], bytes] = lambda u: _get(u, timeout=30)):
        self.z = z
        self.cache_dir = cache_dir
        self.log = log
        self.fetch = fetch
        self.tiles: Dict[Tuple[int, int], Tuple[int, int, int, bytearray]] = {}
        self.lock = threading.Lock()

    def _tile(self, tx: int, ty: int):
        key = (tx, ty)
        if key in self.tiles:
            return self.tiles[key]
        path = self.cache_dir / str(self.z) / str(tx) / f"{ty}.png" if self.cache_dir else None
        data = None
        if path is not None and path.exists():
            data = path.read_bytes()
        if data is None:
            last = None
            for tpl in TERRARIUM_URLS:
                try:
                    data = self.fetch(tpl.format(z=self.z, x=tx, y=ty))
                    break
                except Exception as exc:
                    last = exc
            if data is None:
                raise RuntimeError(f"Ubin elevasi tidak bisa diunduh: {last}")
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        dec = png_decode(data)
        with self.lock:
            self.tiles[key] = dec
        return dec

    def _pixel(self, gx: int, gy: int) -> float:
        tx, px = divmod(gx, 256)
        ty, py = divmod(gy, 256)
        w, _, ch, pix = self._tile(tx, ty)
        i = (py * w + px) * ch
        return pix[i] * 256.0 + pix[i + 1] + pix[i + 2] / 256.0 - 32768.0

    def __call__(self, points: Sequence[Tuple[float, float]]) -> List[float]:
        # Unduh semua ubin yang diperlukan secara paralel dulu
        need = set()
        for lat, lon in points:
            x, y = _tile_xy(lat, lon, self.z)
            for dx in (-0.01, 0.01):
                for dy in (-0.01, 0.01):
                    need.add((int(x + dx), int(y + dy)))
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(lambda k: self._tile(*k), sorted(need)))
        out = []
        for lat, lon in points:
            x, y = _tile_xy(lat, lon, self.z)
            fx, fy = x * 256 - 0.5, y * 256 - 0.5  # pusat piksel
            x0, y0 = int(math.floor(fx)), int(math.floor(fy))
            ax, ay = fx - x0, fy - y0
            v = ((1 - ax) * (1 - ay) * self._pixel(x0, y0) + ax * (1 - ay) * self._pixel(x0 + 1, y0)
                 + (1 - ax) * ay * self._pixel(x0, y0 + 1) + ax * ay * self._pixel(x0 + 1, y0 + 1))
            out.append(round(max(0.0, v), 1))  # nilai negatif = laut/batimetri
        return out


class GridDEM:
    """Elevasi dari layanan titik (Open-Meteo) yang disampel pada grid lalu diinterpolasi bilinear."""

    name = "Elevasi: Open-Meteo (Copernicus DEM GLO-90)"

    def __init__(self, spacing_m: float = 90.0, log: Log = print,
                 fetch: Callable[[str], bytes] = lambda u: _get(u, timeout=30)):
        self.spacing = spacing_m
        self.log = log
        self.fetch = fetch

    def _batch(self, pts: List[Tuple[float, float]]) -> List[float]:
        lats = ",".join(f"{a:.5f}" for a, _ in pts)
        lons = ",".join(f"{b:.5f}" for _, b in pts)
        url = f"https://api.open-meteo.com/v1/elevation?latitude={lats}&longitude={lons}"
        for attempt in range(4):
            try:
                res = json.loads(self.fetch(url).decode())
                return [0.0 if v is None else float(v) for v in res["elevation"]]
            except Exception as exc:
                if attempt == 3:
                    raise RuntimeError(f"Elevasi Open-Meteo gagal: {exc}")
                time.sleep(1.5 * (attempt + 1))
        return []

    def __call__(self, points: Sequence[Tuple[float, float]]) -> List[float]:
        lats = [p[0] for p in points]
        lons = [p[1] for p in points]
        s, n, w, e = min(lats), max(lats), min(lons), max(lons)
        dlat = self.spacing / 111320.0
        dlon = self.spacing / (111320.0 * max(0.2, math.cos(math.radians((s + n) / 2))))
        ny = max(2, int(math.ceil((n - s) / dlat)) + 1)
        nx = max(2, int(math.ceil((e - w) / dlon)) + 1)
        grid_pts = [(s + j * dlat, w + i * dlon) for j in range(ny) for i in range(nx)]
        self.log(f"    elevasi grid {nx}×{ny} titik")
        batches = [grid_pts[k:k + 100] for k in range(0, len(grid_pts), 100)]
        with ThreadPoolExecutor(max_workers=3) as ex:
            vals = [v for part in ex.map(self._batch, batches) for v in part]
        out = []
        for lat, lon in points:
            fy, fx = (lat - s) / dlat, (lon - w) / dlon
            j0, i0 = min(ny - 2, int(fy)), min(nx - 2, int(fx))
            ay, ax = fy - j0, fx - i0
            g = lambda j, i: vals[j * nx + i]  # noqa: E731
            v = ((1 - ax) * (1 - ay) * g(j0, i0) + ax * (1 - ay) * g(j0, i0 + 1)
                 + (1 - ax) * ay * g(j0 + 1, i0) + ax * ay * g(j0 + 1, i0 + 1))
            out.append(round(max(0.0, v), 1))
        return out


class FallbackDEM:
    """Coba beberapa sumber elevasi berurutan; catat sumber yang berhasil."""

    def __init__(self, providers: List, log: Log = print):
        self.providers = providers
        self.log = log
        self.used = None

    def __call__(self, points):
        last = None
        for p in self.providers:
            try:
                vals = p(points)
                self.used = p.name
                return vals
            except Exception as exc:
                last = exc
                self.log(f"    {p.name} gagal: {exc}")
        raise RuntimeError(f"Semua sumber elevasi gagal: {last}")


def default_dem(cache_dir: Optional[Path], log: Log = print) -> FallbackDEM:
    return FallbackDEM([TerrariumDEM(cache_dir, log=log), GridDEM(log=log)], log=log)


# ---------------------------------------------------------------------------
# Nama tempat
# ---------------------------------------------------------------------------
def reverse_place(lat: float, lon: float, timeout: float = 10) -> Tuple[Optional[str], Optional[str]]:
    """(nama tampilan, provinsi) dari Nominatim; (None, None) bila gagal. Provinsi dipakai untuk zona waktu area."""
    try:
        url = ("https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=14&accept-language=id"
               f"&lat={lat:.5f}&lon={lon:.5f}")
        a = json.loads(_get(url, timeout=timeout).decode()).get("address", {})
        local = a.get("village") or a.get("suburb") or a.get("neighbourhood") or a.get("hamlet") or a.get("quarter")
        city = a.get("city") or a.get("town") or a.get("municipality") or a.get("county") or a.get("regency")
        parts = [x for x in (local, city) if x]
        return (", ".join(dict.fromkeys(parts)) or a.get("state")), a.get("state")
    except Exception:
        return None, None


def reverse_name(lat: float, lon: float, timeout: float = 10) -> Optional[str]:
    return reverse_place(lat, lon, timeout)[0]
