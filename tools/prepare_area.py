"""Bangun data area SEMPAT dari data asli (jalankan di komputer yang terhubung internet).

Contoh:
    python tools/prepare_area.py --id tambakrejo --name "Desa Tambakrejo, Malang" \
        --bbox -8.4300,112.7000,-8.3900,112.7600

    # Dengan peta bahaya resmi (GeoJSON poligon) dan titik kumpul resmi (CSV: name,kind,lat,lon)
    python tools/prepare_area.py --id tambakrejo --name "Desa Tambakrejo" --bbox ... \
        --hazard-geojson bahaya.geojson --official-csv titik_kumpul.csv

    # Bila Overpass API sibuk: unduh manual dari https://overpass-turbo.eu (Export > raw OSM data JSON)
    python tools/prepare_area.py --id tambakrejo --name "..." --overpass-json osm.json

Sumber data:
- Jalan, bangunan, garis pantai, sungai : OpenStreetMap (© kontributor OpenStreetMap, ODbL)
- Elevasi                               : SRTM 30 m via OpenTopoData (atau GeoTIFF lokal, mis. DEMNAS)
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import tsunami  # noqa: E402
from backend.area_builder import DEFAULT_PARAMS, build_area, validate_area  # noqa: E402
from backend.config import AREAS_DIR  # noqa: E402

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
UA = "SEMPAT-prepare/0.1 (hackathon INCEPTION 2026)"


def overpass_query(s: float, w: float, n: float, e: float, coast_pad: float = 0.05) -> str:
    b = f"{s},{w},{n},{e}"
    cb = f"{s - coast_pad},{w - coast_pad},{n + coast_pad},{e + coast_pad}"
    return f"""[out:json][timeout:180];
(
  way["highway"]({b});
  way["building"]({b});
  relation["building"]["type"="multipolygon"]({b});
  way["building:part"]({b});
  way["natural"="coastline"]({cb});
  way["waterway"~"^(river|stream|canal)$"]({b});
);
out geom;"""


def fetch_overpass(query: str) -> dict:
    last = None
    for url in OVERPASS_URLS:
        for attempt in range(2):
            try:
                print(f"  Overpass: {url} (percobaan {attempt + 1})")
                data = urllib.parse.urlencode({"data": query}).encode()
                req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=240) as r:
                    return json.loads(r.read().decode("utf-8"))
            except Exception as exc:
                last = exc
                print(f"    gagal: {exc}")
                time.sleep(5)
    raise RuntimeError(f"Semua server Overpass gagal: {last}. Coba lagi nanti atau gunakan --overpass-json.")


class OpenTopoData:
    """Elevasi dari api.opentopodata.org (maks 100 titik/permintaan, 1 permintaan/detik)."""

    def __init__(self, dataset: str, cache_path: Path):
        self.dataset = dataset
        self.cache_path = cache_path
        self.cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    def __call__(self, points):
        keys = [f"{lat:.6f},{lon:.6f}" for lat, lon in points]
        todo = [k for k in dict.fromkeys(keys) if k not in self.cache]
        for i in range(0, len(todo), 100):
            batch = todo[i:i + 100]
            url = f"https://api.opentopodata.org/v1/{self.dataset}?locations={'|'.join(batch)}"
            for attempt in range(5):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": UA})
                    with urllib.request.urlopen(req, timeout=60) as r:
                        res = json.loads(r.read().decode())
                    if res.get("status") != "OK":
                        raise RuntimeError(res.get("error", "status bukan OK"))
                    for k, item in zip(batch, res["results"]):
                        el = item.get("elevation")
                        self.cache[k] = 0.0 if el is None else float(el)  # None = laut
                    break
                except Exception as exc:
                    wait = 2 * (attempt + 1)
                    print(f"    elevasi gagal ({exc}); ulang dalam {wait} s")
                    time.sleep(wait)
            else:
                raise RuntimeError("Gagal mengambil elevasi setelah 5 percobaan.")
            print(f"    elevasi {min(i + 100, len(todo))}/{len(todo)}")
            self.cache_path.write_text(json.dumps(self.cache), encoding="utf-8")
            time.sleep(1.1)
        return [self.cache[k] for k in keys]


class GeoTiffElevation:
    """Elevasi dari GeoTIFF lokal (mis. DEMNAS). Membutuhkan: pip install rasterio"""

    def __init__(self, path: str):
        try:
            import rasterio  # type: ignore
        except ImportError:
            sys.exit("Untuk --elev-geotiff pasang dulu: pip install rasterio")
        self.ds = rasterio.open(path)

    def __call__(self, points):
        vals = []
        for (v,) in self.ds.sample([(lon, lat) for lat, lon in points]):
            vals.append(0.0 if v is None or v < -100 else float(v))
        return vals


def load_hazard(path: str):
    gj = json.loads(Path(path).read_text(encoding="utf-8"))
    rings = []
    feats = gj["features"] if gj.get("type") == "FeatureCollection" else [gj]
    for f in feats:
        g = f["geometry"] if "geometry" in f else f
        if g["type"] == "Polygon":
            rings.append([tuple(c[:2]) for c in g["coordinates"][0]])
        elif g["type"] == "MultiPolygon":
            for poly in g["coordinates"]:
                rings.append([tuple(c[:2]) for c in poly[0]])
    if not rings:
        sys.exit("GeoJSON bahaya tidak memuat Polygon/MultiPolygon.")
    return rings


def load_official(path: str):
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            out.append({"name": row["name"], "kind": row.get("kind", "titik_kumpul"),
                        "lat": float(row["lat"]), "lon": float(row["lon"])})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Siapkan data area SEMPAT dari OpenStreetMap + elevasi")
    ap.add_argument("--id", required=True, help="ID singkat area, mis. tambakrejo")
    ap.add_argument("--name", required=True, help="Nama tampilan area")
    ap.add_argument("--bbox", help="selatan,barat,utara,timur (derajat desimal)")
    ap.add_argument("--overpass-json", help="Pakai respons Overpass yang sudah diunduh")
    ap.add_argument("--elev-dataset", default="srtm30m", help="Dataset OpenTopoData (srtm30m, aster30m)")
    ap.add_argument("--elev-geotiff", help="GeoTIFF elevasi lokal (mis. DEMNAS) sebagai ganti OpenTopoData")
    ap.add_argument("--hazard-geojson", help="Poligon zona bahaya resmi (InaRISK/BPBD)")
    ap.add_argument("--official-csv", help="CSV titik kumpul/TES resmi: name,kind,lat,lon")
    ap.add_argument("--elev-threshold", type=float, default=DEFAULT_PARAMS["hazard_elev_threshold_m"])
    ap.add_argument("--coast-dist", type=float, default=DEFAULT_PARAMS["hazard_max_coast_dist_m"])
    ap.add_argument("--persons-per-house", type=float, default=DEFAULT_PARAMS["persons_per_house"])
    ap.add_argument("--vulnerable-share", type=float, default=DEFAULT_PARAMS["vulnerable_share"])
    ap.add_argument("--eta", type=float, default=DEFAULT_PARAMS["default_eta_min"],
                    help="Perkiraan waktu tiba tsunami (menit) dari kajian BMKG/BPBD")
    ap.add_argument("--timezone", help="Zona waktu area: Asia/Jakarta (WIB), Asia/Makassar (WITA), Asia/Jayapura (WIT). "
                                       "Dipakai untuk jam buka gedung saat peringatan; tanpa ini zona ditebak dari bujur.")
    ap.add_argument("--province", help="Nama provinsi (alternatif --timezone), mis. 'Jawa Timur'")
    args = ap.parse_args()
    tz = args.timezone or tsunami.timezone_for_province(args.province)
    if (args.timezone or args.province) and not tsunami.tz_offsets(tz):
        sys.exit("Zona waktu/provinsi tidak dikenal. Contoh: --timezone Asia/Jakarta atau --province 'Jawa Timur'")

    out_dir = AREAS_DIR / args.id
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    if args.overpass_json:
        overpass = json.loads(Path(args.overpass_json).read_text(encoding="utf-8"))
    else:
        if not args.bbox:
            sys.exit("Isi --bbox selatan,barat,utara,timur atau --overpass-json")
        s, w, n, e = [float(x) for x in args.bbox.split(",")]
        if not (s < n and w < e):
            sys.exit("Urutan --bbox harus selatan,barat,utara,timur")
        print("1/3 Mengunduh data OpenStreetMap ...")
        overpass = fetch_overpass(overpass_query(s, w, n, e))
        (raw_dir / "osm.json").write_text(json.dumps(overpass), encoding="utf-8")

    print("2/3 Menyiapkan elevasi ...")
    if args.elev_geotiff:
        elev = GeoTiffElevation(args.elev_geotiff)
        elev_src = f"Elevasi: GeoTIFF lokal ({Path(args.elev_geotiff).name})"
    else:
        elev = OpenTopoData(args.elev_dataset, raw_dir / f"elev_{args.elev_dataset}.json")
        elev_src = f"Elevasi: {args.elev_dataset} via OpenTopoData"

    hazard = load_hazard(args.hazard_geojson) if args.hazard_geojson else None
    official = load_official(args.official_csv) if args.official_csv else []
    params = {"hazard_elev_threshold_m": args.elev_threshold, "hazard_max_coast_dist_m": args.coast_dist,
              "persons_per_house": args.persons_per_house, "vulnerable_share": args.vulnerable_share,
              "default_eta_min": args.eta}

    print("3/3 Membangun graf jalan kaki, zona bahaya, dan bangunan ...")
    area = build_area(overpass, elev, area_id=args.id, name=args.name, params=params,
                      hazard_polygons=hazard, official_points=official,
                      sources=["Jalan, bangunan, pantai: © kontributor OpenStreetMap (ODbL)", elev_src,
                               "Zona bahaya: " + ("poligon resmi" if hazard else
                                                  f"aturan elevasi < {args.elev_threshold} m dan jarak pantai < "
                                                  f"{args.coast_dist / 1000:.1f} km (ASUMSI, ganti dengan peta resmi)")])
    if tz:
        area["meta"].update(timezone=tz, timezone_src="eksplisit")
    else:
        print("  PERINGATAN: zona waktu tidak diisi (--timezone/--province); jam buka gedung memakai perkiraan bujur.")
    warns = validate_area(area)
    for wmsg in warns:
        print("  PERINGATAN:", wmsg)
    (out_dir / "area.json").write_text(json.dumps(area, separators=(",", ":")), encoding="utf-8")
    print(f"\nSelesai: {out_dir / 'area.json'}")
    print(f"Jalankan: python run.py --area {args.id}")


if __name__ == "__main__":
    main()
