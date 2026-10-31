"""Membangun data area (graf jalan kaki, bangunan, zona bahaya) dari data OpenStreetMap.

Masukan berformat sama dengan keluaran Overpass API (`out geom;`), sehingga
pipeline yang sama dipakai untuk data asli (tools/prepare_area.py) maupun
desa sintetis (backend/synthetic.py).
"""
from __future__ import annotations

import datetime as _dt
import math
import re
from collections import deque
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .geo import (LocalProjection, SegmentGrid, haversine_m, point_in_polygon,
                  polygon_area_m2, polygon_centroid)
from .tsunami import osm_unsafe_reason, refresh_flags, structure_from_tags

# Jalan yang tidak boleh/tidak realistis dilalui pejalan kaki.
EXCLUDED_HIGHWAY = {
    "motorway", "motorway_link", "construction", "proposed", "abandoned",
    "platform", "raceway", "bus_guideway", "escape", "razed", "corridor",
}
PATH_HIGHWAY = {"footway", "path", "pedestrian", "track", "bridleway", "cycleway"}
# Dipakai HANYA untuk perkiraan penduduk (aturan lama, sengaja tidak diubah agar angka "warga tidak sempat"
# tetap sebanding). Bukan dasar kelayakan shelter: lihat _building_kind.
RESIDENTIAL_BUILDING = {
    "yes", "house", "residential", "detached", "semidetached_house", "terrace",
    "apartments", "hut", "bungalow", "farm",
}
# Klasifikasi fungsi gedung untuk evakuasi vertikal (nilai tag OSM `building`).
HOUSE_BUILDING = {"house", "detached", "semidetached_house", "terrace", "hut", "bungalow", "farm", "residential",
                  "cabin", "static_caravan", "houseboat"}
APARTMENT_BUILDING = {"apartments", "dormitory"}
RELIGIOUS_BUILDING = {"mosque", "church", "temple", "chapel", "cathedral", "shrine", "synagogue", "religious"}
EDU_BUILDING = {"school", "college", "university", "kindergarten"}
HEALTH_BUILDING = {"hospital", "clinic"}
GOV_BUILDING = {"government", "public", "civic", "townhall", "fire_station"}
COMMERCIAL_BUILDING = {"commercial", "retail", "office", "supermarket"}
OTHER_BUILDING = {"industrial", "warehouse", "factory", "manufacture", "garage", "garages", "shed", "roof", "carport",
                  "parking", "service", "transformer_tower", "toilets", "greenhouse", "kiosk", "bunker", "container",
                  "ruins", "construction", "stadium", "sports_hall", "train_station", "transportation", "barn"}
KIND_LABEL = {"rumah": "Rumah", "apartemen": "Apartemen/hunian bersama", "sekolah": "Sekolah",
              "ibadah": "Tempat ibadah", "kantor": "Kantor pemerintah/publik", "kesehatan": "Fasilitas kesehatan",
              "hotel": "Hotel", "komersial": "Gedung komersial/kantor", "lainnya": "Bangunan lain",
              "tidak_diketahui": "Fungsi tidak diketahui"}
FEET_M = 0.3048

DEFAULT_PARAMS = {
    # Aturan zona bahaya cadangan bila peta bahaya resmi belum tersedia.
    # NILAI INI ASUMSI DEMO: ganti dengan peta bahaya InaRISK/BMKG/BPBD.
    "hazard_elev_threshold_m": 20.0,
    "hazard_max_coast_dist_m": 3000.0,
    "safe_buffer_m": 2.0,          # target aman = elevasi >= ambang + max(buffer, galat DEM)
    # Galat vertikal DEM global (SRTM/Copernicus) di pesisir datar bisa beberapa meter: target aman
    # dinaikkan sebesar ini agar "tanah tinggi" tidak ternyata lebih rendah dari perkiraan. ASUMSI.
    "dem_uncertainty_m": 5.0,
    # Skenario rencana untuk gedung evakuasi vertikal bila peringatan tidak memuat tinggi gelombang
    # (BMKG "Awas" = > 3 m). ASUMSI DEMO: ganti dengan tinggi rencana dari kajian risiko BPBD/InaRISK.
    "tsunami_design_height_m": 10.0,
    "underpass_penalty": 10.0,     # terowongan/underpass di zona bahaya: tergenang lebih dulu
    "bridge_penalty": 3.0,         # jembatan dekat pantai: tsunami naik lewat sungai, jembatan bisa hanyut
    "bridge_coast_m": 1500.0,
    "hazard_edge_buffer_m": 50.0,  # peta bahaya resmi: target aman minimal sejauh ini dari batas zona
    "persons_per_house": 4.0,      # asumsi demo; ganti dengan data kependudukan desa
    "vulnerable_share": 0.15,      # asumsi demo; proporsi lansia/disabilitas/balita
    "shelter_min_levels": 2,
    "shelter_usable_factor": 0.5,  # FEMA P-646: 50-85% luas kotor dapat dipakai
    "space_per_person_m2": 0.93,   # FEMA P-646: 10 sq ft per orang
    "default_eta_min": 20.0,       # asumsi demo bila peringatan tidak memuat estimasi
    # Alokasi berkapasitas (rute warga & perencana): anggaran waktu = (ETA - jeda mulai - margin) menit. Nilai bawaan
    # SAMA dengan penggeser perencana (5 & 2 menit) agar perencana dan rute warga memakai anggaran yang sama. ASUMSI.
    "alloc_delay_min": 5.0,
    "alloc_margin_min": 2.0,
    # Alokasi: utamakan sesedikit mungkin orang tiba setelah batas waktu sebelum total waktu (docs/capacity/DESIGN.md).
    "alloc_prioritize_deadline": True,
    # Jumlah lantai bila hanya ada TINGGI gedung (OSM height, Google 2.5D, unggahan): floor(tinggi / nilai ini).
    # KEPUTUSAN PROYEK (bukan standar): 4 m per lantai, sengaja besar agar jumlah lantai TIDAK dilebih-lebihkan
    # (gedung tidak tampak lebih aman dari kenyataan). Hasilnya selalu ditandai levels_est (perkiraan).
    "height_per_level_m": 4.0,
    # Batas kewajaran: building:levels yang menyiratkan < 2 m per lantai dianggap konflik dengan height. ASUMSI.
    "min_level_height_m": 2.0,
    # Jalan acuan elevasi gedung paling jauh 150 m: jalan yang lebih jauh TIDAK dipakai sebagai elevasi gedung.
    # ASUMSI. DEM vs jalan yang berselisih > dem_uncertainty_m ditandai kepercayaan "rendah".
    "building_elev_road_max_m": 150.0,
    # Margin galat elevasi (m) untuk lantai aman KONSERVATIF per kepercayaan elevasi gedung (tsunami.assess).
    # KEPUTUSAN PROYEK 1 Nov 2026 (parameter awal, perlu divalidasi BPBD/data elevasi lebih akurat):
    # tinggi (diukur petugas) 0 m, sedang 3 m, rendah 5 m; elevasi tidak tersedia dihitung 0 m dan tidak bisa verified.
    "elev_margin_m": {"tinggi": 0.0, "sedang": 3.0, "rendah": 5.0, "tidak_ada": 0.0},
}

ElevationProvider = Callable[[Sequence[Tuple[float, float]]], List[float]]
# 2: sumber jumlah lantai, struktur, basement, tanda ruas (terowongan/jembatan)
# 3: klasifikasi fungsi baru, tingkat kepastian lantai/elevasi, multipolygon & building:part, kapasitas dari assess
# 4: 4 m per lantai, atap/kubah, elevasi gedung (sumber, jarak, kepercayaan), konflik data lantai
# 5: tag OSM building + alasan Tidak Aman (osm_unsafe), tier tersimpan, margin elevasi per kepercayaan
AREA_SCHEMA = 5


def upgrade_area(area: dict) -> dict:
    """Lengkapi data area versi lama agar tetap bisa dipakai.

    Catatan: area lama tidak menyimpan tag OSM, jadi jenis gedung (mis. building=yes yang dulu "rumah") tidak bisa
    diklasifikasi ulang di sini. Peta otomatis & area analisis dibangun ulang otomatis; desa yang disiapkan perlu
    tools/prepare_area.py dijalankan lagi.
    """
    schema = area.get("meta", {}).get("schema", 1)
    if schema >= AREA_SCHEMA:
        return area
    params = area.setdefault("params", {})
    if schema < 4 and params.get("height_per_level_m") in (None, 3.5):
        params["height_per_level_m"] = DEFAULT_PARAMS["height_per_level_m"]  # nilai bawaan lama 3,5 m -> 4 m
    for k, v in DEFAULT_PARAMS.items():
        params.setdefault(k, v)
    for b in area.get("buildings", []):
        if schema < 2:
            b.setdefault("levels_src", "osm" if b.get("levels", 1) > 1 else "default")
            b.setdefault("height_m", None)
            b.setdefault("basement", 0)
            b.setdefault("structure", "tidak_tahu")
        b.setdefault("levels_est", b.get("levels_src") in ("osm_height", "google")
                     or (b.get("levels_src") == "upload" and b.get("height_m") is not None))
        b.setdefault("geom", "way")
        if schema < 4:
            _upgrade_levels_v4(b, params)
            if "elev_conf" not in b or "elev_src" not in b:  # skema lama: elevasi = interpolasi jalan (elev_road) dan/atau DEM di gedung
                road = b.get("elev_road", b.get("elev"))
                b.update(building_elevation(_finite(b.get("elev_dem")), _finite(road), _finite(b.get("perp_m")), params))
        if schema < 5:
            # Area lama tidak menyimpan tag OSM: kategori Tidak Aman (construction/ruins/...) tidak bisa diperiksa.
            # None = "tidak diketahui" -> caveat, dan peta otomatis/area analisis dibangun ulang otomatis.
            b.setdefault("osm_unsafe", None)
        refresh_flags(b, params)
    area["edges"].setdefault("flag", [""] * len(area["edges"]["u"]))
    area["meta"]["schema"] = AREA_SCHEMA
    return area


def _upgrade_levels_v4(b: dict, params: dict) -> None:
    """Hitung ulang jumlah lantai PERKIRAAN dari height_m yang tersimpan dengan aturan per lantai yang berlaku."""
    h = _finite(b.get("height_m"))
    if not (b.get("levels_est") and h and h > 0):
        return
    src = b.get("levels_src")
    if b.get("kind") == "ibadah" and src in ("osm_height", "google", "upload"):
        b.update(levels=1, levels_src="default", levels_est=False, levels_note=RELIGIOUS_HEIGHT_NOTE)
        return
    new = levels_from_height(h, params.get("height_per_level_m"))
    if src == "osm":  # building:levels bertentangan dengan height: tetap nilai terkecil
        b["levels"] = min(int(b.get("levels") or 1), new)
    elif src in ("osm_height", "osm_part", "google", "upload"):
        b["levels"] = new


def building_elevation(dem_b: Optional[float], road_elev: Optional[float], perp_m: Optional[float],
                       params: dict) -> dict:
    """Elevasi tanah gedung untuk menghitung genangan dan lantai aman.

    Urutan: DEM DI TITIK GEDUNG diutamakan. Bila ada jalan acuan dekat (<= building_elev_road_max_m), dipakai nilai
    yang lebih rendah: DEM global (SRTM/Copernicus) mengukur permukaan dan bisa ikut atap gedung, sedangkan nilai
    lebih rendah = genangan lebih dalam = lantai aman lebih tinggi (konservatif). Jalan yang lebih jauh TIDAK pernah
    dipakai sebagai elevasi gedung. Tanpa DEM dan tanpa jalan dekat: elev None (dihitung 0 m oleh tsunami.assess).

    elev_conf: "sedang" (DEM & jalan dekat cocok dalam galat DEM), "rendah" (satu sumber saja / berselisih),
    "tidak_ada". Tidak ada tingkat "tinggi": akurasi DEM belum divalidasi lapangan.
    """
    max_d = float(params.get("building_elev_road_max_m", 150.0))
    unc = float(params.get("dem_uncertainty_m", 5.0))
    near = road_elev is not None and perp_m is not None and perp_m <= max_d
    if dem_b is not None and near:
        elev = min(dem_b, road_elev)
        src = "jalan_dekat" if road_elev < dem_b else "dem_gedung"
        conf = "sedang" if abs(dem_b - road_elev) <= unc else "rendah"
    elif dem_b is not None:
        elev, src, conf = dem_b, "dem_gedung", "rendah"
    elif near:
        elev, src, conf = road_elev, "jalan_dekat", "rendah"
    else:
        elev, src, conf = None, "tidak_tersedia", "tidak_ada"
    return {"elev": round(elev, 1) if elev is not None else None, "elev_src": src, "elev_conf": conf,
            "elev_uncertain": conf != "sedang",
            "elev_road": round(road_elev, 1) if road_elev is not None else None,
            "elev_dem": round(dem_b, 1) if dem_b is not None else None,
            "elev_dist_m": round(perp_m, 1) if perp_m is not None else None}


def _walkable(tags: dict) -> bool:
    hw = tags.get("highway")
    if not hw or hw in EXCLUDED_HIGHWAY:
        return False
    if tags.get("foot") == "no" or tags.get("access") in ("private", "no"):
        return False
    if tags.get("area") == "yes":
        return False
    return True


def _edge_kind(tags: dict) -> str:
    hw = tags.get("highway", "")
    if hw == "steps":
        return "steps"
    if hw in PATH_HIGHWAY:
        return "path"
    return "road"


def _legacy_dwelling(tags: dict) -> bool:
    """Aturan lama 'rumah' (termasuk building=yes/apartments) — HANYA untuk perkiraan jumlah penduduk."""
    b = tags.get("building", "")
    amenity = tags.get("amenity", "")
    if amenity in ("place_of_worship", "school", "college", "university", "townhall", "police", "fire_station",
                   "hospital", "clinic", "doctors") or tags.get("tourism") in ("hotel", "guest_house"):
        return False
    if b in ("mosque", "church", "temple", "chapel", "school", "college", "university", "government", "public",
             "civic", "hospital", "clinic", "hotel"):
        return False
    return b in RESIDENTIAL_BUILDING


def _building_kind(tags: dict, levels: Optional[int] = None) -> str:
    """Fungsi gedung dari tag OSM: building, amenity, healthcare, office, shop, tourism, building:use.

    building=yes tanpa petunjuk lain = "tidak_diketahui" (BUKAN rumah, BUKAN otomatis layak).
    """
    b = str(tags.get("building", "")).strip().lower()
    amenity = tags.get("amenity", "")
    use = str(tags.get("building:use", "")).strip().lower()
    if amenity == "place_of_worship" or b in RELIGIOUS_BUILDING or use == "religious":
        return "ibadah"
    if amenity in ("school", "college", "university", "kindergarten") or b in EDU_BUILDING or use == "education":
        return "sekolah"
    if (amenity in ("hospital", "clinic", "doctors") or tags.get("healthcare") in ("hospital", "clinic", "centre")
            or b in HEALTH_BUILDING or use in ("healthcare", "hospital")):
        return "kesehatan"
    if (amenity in ("townhall", "police", "fire_station", "courthouse") or b in GOV_BUILDING
            or tags.get("office") == "government" or use in ("government", "public", "civic")):
        return "kantor"
    if tags.get("tourism") in ("hotel", "guest_house", "hostel", "motel") or b == "hotel":
        return "hotel"
    if b in APARTMENT_BUILDING or use in ("apartments", "dormitory"):
        return "apartemen"
    if b in HOUSE_BUILDING:
        return "rumah"
    if b in COMMERCIAL_BUILDING or tags.get("shop") or tags.get("office") or use in ("commercial", "retail", "office"):
        return "komersial"
    if use == "residential":  # building=yes + building:use=residential: bertingkat tinggi = hunian bersama
        return "apartemen" if (levels or 1) >= 3 else "rumah"
    if b in OTHER_BUILDING:
        return "lainnya"
    return "tidak_diketahui"


_NUM = r"(\d+(?:\.\d+)?)"


def parse_levels(val) -> Optional[int]:
    """Jumlah lantai dari tag (building:levels). '3' -> 3, '3.5' -> 3 (dibulatkan ke bawah), '3;4' -> 3, '3-4' -> 3.

    Nilai tidak masuk akal (< 1, > 300, teks) -> None. Selalu memilih nilai terkecil (konservatif).
    """
    if val is None:
        return None
    out = []
    for part in str(val).replace(",", ".").split(";"):
        m = re.fullmatch(r"\s*" + _NUM + r"\s*(?:-\s*" + _NUM + r"\s*)?", part)
        if m:
            out.append(float(m.group(1)))
    if not out:
        return None
    n = int(math.floor(min(out)))
    return n if 1 <= n <= 300 else None


def parse_height_m(val) -> Optional[float]:
    """Tinggi gedung dalam meter. Mendukung '15', '15 m', '15.5m', '15,5', '45 ft', '45 feet', '12\\'6"'.

    Satuan lain/teks -> None. Beberapa nilai ('12;15') -> terkecil (konservatif).
    """
    if val is None:
        return None
    out = []
    for part in str(val).strip().lower().replace(",", ".").split(";"):
        part = part.strip()
        m = re.fullmatch(_NUM + r"\s*(m|meter|meters|metre|metres)?", part)
        if m:
            out.append(float(m.group(1)))
            continue
        m = re.fullmatch(_NUM + r"\s*(ft|feet|foot)", part)
        if m:
            out.append(float(m.group(1)) * FEET_M)
            continue
        m = re.fullmatch(r"(\d+)\s*'\s*(?:" + _NUM + r"\s*\")?", part)
        if m:
            out.append(int(m.group(1)) * FEET_M + float(m.group(2) or 0) * 0.0254)
    if not out:
        return None
    h = min(out)
    return round(h, 2) if 0 < h < 1000 else None


def _num(val) -> Optional[float]:
    """Angka polos (layer, building:levels:underground)."""
    try:
        return float(str(val).split(";")[0].replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def levels_from_height(h: float, per_level_m: Optional[float] = None) -> int:
    """PERKIRAAN jumlah lantai dari tinggi gedung: floor(tinggi / per_level_m), hanya lantai utuh, minimal 1.

    per_level_m = params["height_per_level_m"] (keputusan proyek: 4 m); hasil harus ditandai levels_est=True.
    Contoh 4 m: 12 m -> 3, 15 m -> 3, 20 m -> 5.
    """
    per = float(per_level_m or DEFAULT_PARAMS["height_per_level_m"])
    return max(1, int(math.floor(h / per + 1e-9)))


# Atap miring: bagian atap tidak berisi lantai yang bisa dipakai. Tanpa roof:height, ±1 lantai dianggap atap.
SLOPED_ROOF = {"gabled", "hipped", "half-hipped", "side_hipped", "gambrel", "mansard", "saltbox", "round",
               "skillion", "crosspitched", "butterfly", "double_saltbox", "quadruple_saltbox"}
# Kubah/menara/kerucut/limas: tinggi atap bisa sangat besar -> tinggi TIDAK dipakai tanpa roof:height.
DOME_ROOF = {"dome", "onion", "cone", "conical", "spire", "pyramidal", "pyramid"}
RELIGIOUS_HEIGHT_NOTE = "tinggi tempat ibadah sering mencakup kubah/menara: jumlah lantai perlu survei"


def _religious(tags: dict) -> bool:
    return (tags.get("amenity") == "place_of_worship" or str(tags.get("building", "")).lower() in RELIGIOUS_BUILDING
            or str(tags.get("building:use", "")).lower() == "religious")


def usable_height(tags: dict, h: float, params: Optional[dict] = None) -> Tuple[Optional[float], str]:
    """Bagian tinggi gedung yang berisi lantai (tinggi OSM = sampai puncak atap). None = tidak bisa diperkirakan."""
    p = params or DEFAULT_PARAMS
    per = float(p.get("height_per_level_m") or DEFAULT_PARAMS["height_per_level_m"])
    rh = parse_height_m(tags.get("roof:height"))
    shape = str(tags.get("roof:shape", "")).strip().lower()
    roof_lv = parse_levels(tags.get("roof:levels"))
    if rh is not None:
        if rh >= h:
            return None, "roof:height tidak lebih kecil dari height: tinggi tidak dipakai"
        return h - rh, f"tinggi atap {rh:g} m tidak dihitung"
    if shape in DOME_ROOF:
        return None, f"atap {shape} tanpa roof:height: tinggi tidak dipakai, perlu survei"
    if shape in SLOPED_ROOF or roof_lv:
        allow = per * max(1, roof_lv or 1)
        return max(h - allow, 0.1), f"±{allow:g} m teratas dianggap atap"
    return h, ""


def _levels(tags: dict, params: Optional[dict] = None) -> Tuple[int, str, Optional[float], bool, str]:
    """(jumlah lantai, sumber, tinggi m, perkiraan?, catatan).

    Sumber: osm (building:levels) | osm_height | <sempat:height_src> | default.
    1. building:levels yang valid = sumber utama; bila bertentangan dengan height (< min_level_height_m per lantai)
       dipakai nilai terkecil dan ditandai perkiraan.
    2. Hanya height: floor(tinggi berisi lantai / height_per_level_m), tinggi atap/kubah tidak dihitung, ditandai
       perkiraan. Tempat ibadah: height TIDAK dipakai (sering = kubah/menara) -> perlu survei.
    """
    p = params or DEFAULT_PARAMS
    per = p.get("height_per_level_m")
    h = parse_height_m(tags.get("height"))
    lv = None
    for key in ("building:levels", "levels"):
        lv = parse_levels(tags.get(key))
        if lv is not None:
            break
    if lv is not None:
        if h is not None and h / lv < float(p.get("min_level_height_m", 2.0)):
            hb = usable_height(tags, h, p)[0]
            est = levels_from_height(hb if hb is not None else h, per)
            return min(lv, est), "osm", h, True, "building:levels bertentangan dengan height; dipakai nilai terkecil"
        return lv, "osm", h, False, ""
    if h is not None:
        if _religious(tags):
            return 1, "default", h, False, RELIGIOUS_HEIGHT_NOTE
        hb, note = usable_height(tags, h, p)
        if hb is None:
            return 1, "default", h, False, note
        src = tags.get("sempat:height_src") or "osm_height"
        return levels_from_height(hb, per), src, h, True, "perkiraan dari tinggi gedung" + (f"; {note}" if note else "")
    return 1, "default", None, False, ""


def _levels_conflict(tags: dict, params: dict) -> Optional[dict]:
    """building:levels vs height yang bertentangan -> {"osm": lantai tag, "osm_height": lantai dari tinggi}."""
    lv = parse_levels(tags.get("building:levels") or tags.get("levels"))
    h = parse_height_m(tags.get("height"))
    if lv and h and h / lv < float(params.get("min_level_height_m", 2.0)):
        return {"osm": lv, "osm_height": levels_from_height(h, params.get("height_per_level_m"))}
    return None


def _underground(tags: dict) -> int:
    v = _num(tags.get("building:levels:underground")) if tags.get("building:levels:underground") else None
    if v and v > 0:
        return int(round(v))
    if tags.get("location") == "underground" or (_num(tags.get("layer")) or 0) < 0:
        return 1
    if tags.get("parking") == "underground":
        return 1
    return 0


def _edge_flag(tags: dict) -> str:
    """'under' = terowongan/underpass, 'bridge' = jembatan, '' = biasa."""
    if tags.get("tunnel") in ("yes", "building_passage", "culvert", "flooded") or tags.get("location") == "underground" \
            or (_num(tags.get("layer")) or 0) < 0:
        return "under"
    if tags.get("bridge") and tags.get("bridge") != "no":
        return "bridge"
    return ""


def _finite(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _assemble_rings(segs: List[List[Tuple[float, float]]]) -> Optional[List[List[Tuple[float, float]]]]:
    """Gabungkan potongan way anggota multipolygon menjadi cincin tertutup (ujung bertemu ujung).

    None bila ada cincin yang tidak bisa ditutup: geometri tidak valid ditolak utuh, bukan dipakai sebagian.
    """
    def key(pt):
        return round(pt[0], 7), round(pt[1], 7)

    pending = [list(s) for s in segs if len(s) >= 2]
    rings = []
    while pending:
        ring = pending.pop(0)
        while key(ring[0]) != key(ring[-1]):
            end = key(ring[-1])
            for i, s in enumerate(pending):
                if key(s[0]) == end:
                    ring += s[1:]
                    break
                if key(s[-1]) == end:
                    ring += list(reversed(s))[1:]
                    break
            else:
                return None
            pending.pop(i)
        if len({key(pt) for pt in ring}) < 3:
            return None
        rings.append(ring)
    return rings


def _merge_relations(buildings_raw: List[dict], relations: List[dict], log) -> List[dict]:
    """Relation multipolygon bertag building -> satu bangunan (cincin luar + lubang).

    Way luar yang JUGA bertag building tidak dihitung dua kali: tagnya digabung ke relation (tag relation menang).
    """
    if not relations:
        return buildings_raw
    by_osm = {b["osm"]: b for b in buildings_raw if b.get("geom") == "way"}
    drop, out, bad = set(), [], 0
    for rel in relations:
        outer, inner, outer_ids = [], [], []
        for m in rel.get("members") or []:
            if m.get("type") != "way":
                continue
            seg = [(g["lat"], g["lon"]) for g in (m.get("geometry") or []) if g]
            if len(seg) < 2:
                continue
            if m.get("role") == "inner":
                inner.append(seg)
            else:
                outer.append(seg)
                outer_ids.append(m.get("ref"))
        outers = _assemble_rings(outer)
        inners = _assemble_rings(inner) if inner else []
        if not outers or inners is None:
            bad += 1
            continue
        tags = dict(rel.get("tags") or {})
        for wid in outer_ids:
            w = by_osm.get(wid)
            if w is not None:
                drop.add(wid)
                for k, v in w["tags"].items():
                    tags.setdefault(k, v)
        tags.pop("type", None)
        out.append({"id": f"r{rel.get('id')}", "osm": None, "tags": tags, "outer": outers, "inner": inners,
                    "geom": "multipolygon"})
    if bad:
        log(f"  {bad} relation bangunan dilewati: cincin multipolygon tidak tertutup/tidak valid.")
    return [b for b in buildings_raw if b.get("osm") not in drop] + out


def _building_geometry(buildings_raw: List[dict], parts_raw: List[dict], proj: LocalProjection, p: dict,
                       log) -> List[dict]:
    """Luas bersih, titik tengah, jumlah lantai. building:part TIDAK menjadi bangunan sendiri (hindari duplikasi);
    bila garis luar tidak punya data lantai, dipakai bagian TERTINGGI (tidak dijumlahkan) dan kapasitas memakai luas
    bagian tertinggi itu saja (konservatif)."""
    geos, bad = [], 0
    for b in buildings_raw:
        rings = []
        for ring in b["outer"]:
            r = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
            if len(r) < 3 or any(_finite(a) is None or _finite(c) is None for a, c in r):
                rings = []
                break
            rings.append(r)
        if not rings:
            bad += 1
            continue
        xys = [[proj.to_xy(a, c) for a, c in r] for r in rings]
        areas = [polygon_area_m2(xy) for xy in xys]
        holes = 0.0
        for ring in b.get("inner") or []:
            r = ring[:-1] if ring[0] == ring[-1] else ring
            holes += polygon_area_m2([proj.to_xy(a, c) for a, c in r])
        net = sum(areas) - holes
        if net < 8:  # terlalu kecil untuk bangunan hunian
            continue
        k = max(range(len(rings)), key=lambda i: areas[i])
        cx, cy = polygon_centroid(xys[k])
        clat, clon = proj.to_latlon(cx, cy)
        xs = [x for xy in xys for x, _ in xy]
        ys = [y for xy in xys for _, y in xy]
        geos.append({"id": b["id"], "tags": b["tags"], "geom": b["geom"], "ring": rings[k], "xys": xys,
                     "box": (min(xs), min(ys), max(xs), max(ys)), "area": net, "cx": cx, "cy": cy,
                     "clat": clat, "clon": clon, "levels": _levels(b["tags"], p), "parts": []})
    if bad:
        log(f"  {bad} bangunan dilewati: geometri tidak valid.")
    if not parts_raw or not geos:
        return _dedupe(geos, log)
    cell = 100.0
    grid: Dict[Tuple[int, int], List[int]] = {}
    for gi, g in enumerate(geos):
        x0, y0, x1, y1 = g["box"]
        for i in range(int(math.floor(x0 / cell)), int(math.floor(x1 / cell)) + 1):
            for j in range(int(math.floor(y0 / cell)), int(math.floor(y1 / cell)) + 1):
                grid.setdefault((i, j), []).append(gi)
    orphan = 0
    for pr in parts_raw:
        c = pr["coords"]
        r = c[:-1] if c[0] == c[-1] else c
        if len(r) < 3:
            continue
        xy = [proj.to_xy(a, b) for a, b in r]
        area = polygon_area_m2(xy)
        lv = _levels(pr["tags"], p)
        if area < 4 or lv[1] == "default":
            continue
        ml = parse_levels(pr["tags"].get("building:min_level")) or 0  # bagian menggantung: lantai di bawahnya kosong
        px, py = polygon_centroid(xy)
        owner = None
        for gi in grid.get((int(math.floor(px / cell)), int(math.floor(py / cell))), ()):
            g = geos[gi]
            if any(point_in_polygon(px, py, ring_xy) for ring_xy in g["xys"]):
                owner = g
                break
        if owner is None:
            orphan += 1
            continue
        owner["parts"].append((lv, area, ml))
    if orphan:
        log(f"  {orphan} building:part tanpa garis luar bangunan diabaikan.")
    for g in geos:
        if not g["parts"] or g["levels"][1] != "default":
            continue  # lantai eksplisit pada garis luar tetap sumber utama
        top = max(lv[0] for lv, _, _ in g["parts"])
        sel = [(lv, a, ml) for lv, a, ml in g["parts"] if lv[0] == top]
        est = any(lv[3] for lv, _, _ in sel)
        height = max((lv[2] for lv, _, _ in sel if lv[2]), default=None)
        g["levels"] = (top, "osm_height" if est else "osm_part", height, est,
                       f"dari {len(g['parts'])} building:part; lantai = bagian tertinggi (tidak dijumlahkan)")
        g["cap_area"] = sum(a for _, a, _ in sel)
        # building:min_level bagian tertinggi: lantai 1..min_level di bawah bagian itu bukan bagian dari luasnya,
        # jadi kapasitas hanya dihitung dari lantai di atasnya (tsunami.assess). Ambil nilai terbesar (konservatif).
        g["min_level"] = max(ml for _, _, ml in sel)
        g["geom"] += "+part"
    return _dedupe(geos, log)


def _dedupe(geos: List[dict], log) -> List[dict]:
    """Buang footprint ganda (dua way/relation untuk gedung yang sama): titik berat < 2 m dan luas berselisih < 15 %.
    Yang dipertahankan: yang tagnya paling banyak (lalu yang pertama). Tidak menggabungkan gedung bersebelahan."""
    cell = 10.0
    idx: Dict[Tuple[int, int], List[int]] = {}
    drop = set()
    order = sorted(range(len(geos)), key=lambda i: -len(geos[i]["tags"]))
    for gi in order:
        g = geos[gi]
        ci, cj = int(g["cx"] // cell), int(g["cy"] // cell)
        dup = False
        for i in (ci - 1, ci, ci + 1):
            for j in (cj - 1, cj, cj + 1):
                for hj in idx.get((i, j), ()):
                    h = geos[hj]
                    if math.hypot(h["cx"] - g["cx"], h["cy"] - g["cy"]) < 2.0 and \
                            abs(h["area"] - g["area"]) <= 0.15 * max(h["area"], g["area"]):
                        dup = True
                        break
                if dup:
                    break
            if dup:
                break
        if dup:
            drop.add(gi)
            continue
        idx.setdefault((ci, cj), []).append(gi)
    if drop:
        log(f"  {len(drop)} footprint ganda dibuang (titik berat < 2 m, luas berselisih < 15 %).")
    return [g for i, g in enumerate(geos) if i not in drop]


def build_area(overpass: dict, elevation: ElevationProvider, *, area_id: str, name: str,
               params: Optional[dict] = None, hazard_polygons: Optional[List[List[Tuple[float, float]]]] = None,
               official_points: Optional[List[dict]] = None, synthetic: bool = False,
               sources: Optional[List[str]] = None, display: Optional[dict] = None,
               log: Callable[[str], None] = print) -> dict:
    """Bangun struktur area dari elemen Overpass.

    hazard_polygons: daftar ring [(lon, lat), ...] dari peta bahaya resmi (opsional).
    """
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    elements = overpass.get("elements", [])

    # ---------- 1. Kumpulkan ruas jalan ----------
    osm_nodes: Dict[int, Tuple[float, float]] = {}
    raw_edges: List[Tuple[int, int, str, str]] = []
    buildings_raw: List[dict] = []
    parts_raw: List[dict] = []
    relations: List[dict] = []
    coast_lines: List[List[Tuple[float, float]]] = []
    water_lines: List[List[Tuple[float, float]]] = []
    for el in elements:
        if el.get("type") == "relation":
            rt = el.get("tags", {}) or {}
            if rt.get("type") == "multipolygon" and rt.get("building") and rt.get("building") != "no":
                relations.append(el)
            continue
        if el.get("type") != "way":
            continue
        tags = el.get("tags", {}) or {}
        geom = el.get("geometry") or []
        nids = el.get("nodes") or []
        coords = [(g["lat"], g["lon"]) for g in geom if g]
        if tags.get("highway") and _walkable(tags) and len(coords) == len(nids) and len(nids) >= 2:
            kind = _edge_kind(tags)
            flag = _edge_flag(tags)
            for nid, c in zip(nids, coords):
                osm_nodes[nid] = c
            for a, b in zip(nids[:-1], nids[1:]):
                if a != b:
                    raw_edges.append((a, b, kind, flag))
        elif tags.get("building") and tags.get("building") != "no" and len(coords) >= 3:
            buildings_raw.append({"id": f"w{el.get('id')}", "osm": el.get("id"), "tags": tags,
                                  "outer": [coords], "inner": [], "geom": "way"})
        elif tags.get("building:part") and tags.get("building:part") != "no" and len(coords) >= 4:
            parts_raw.append({"tags": tags, "coords": coords})
        elif tags.get("natural") == "coastline" and len(coords) >= 2:
            coast_lines.append(coords)
        elif tags.get("waterway") in ("river", "stream", "canal") and len(coords) >= 2:
            water_lines.append(coords)

    if not raw_edges:
        raise ValueError("Tidak ada ruas jalan yang bisa dilalui pejalan kaki di area ini.")
    buildings_raw = _merge_relations(buildings_raw, relations, log)

    # ---------- 2. Graf: indeks node, hapus duplikat, ambil komponen terbesar ----------
    adj: Dict[int, set] = {}
    edge_kind: Dict[Tuple[int, int], str] = {}
    edge_flag: Dict[Tuple[int, int], str] = {}
    for a, b, kind, flag in raw_edges:
        key = (a, b) if a < b else (b, a)
        prev = edge_kind.get(key)
        # Bila dua ruas berimpit, utamakan jenis yang paling mudah dilalui.
        rank = {"road": 0, "path": 1, "steps": 2}
        if prev is None or rank[kind] < rank[prev]:
            edge_kind[key] = kind
        # ...dan untuk tanda bahaya, yang paling tidak berisiko (ruas biasa mengalahkan terowongan).
        frank = {"": 0, "bridge": 1, "under": 2}
        if key not in edge_flag or frank[flag] < frank[edge_flag[key]]:
            edge_flag[key] = flag
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    # Komponen terhubung terbesar
    seen: set = set()
    best_comp: List[int] = []
    for start in adj:
        if start in seen:
            continue
        comp = []
        dq = deque([start])
        seen.add(start)
        while dq:
            x = dq.popleft()
            comp.append(x)
            for y in adj[x]:
                if y not in seen:
                    seen.add(y)
                    dq.append(y)
        if len(comp) > len(best_comp):
            best_comp = comp
    keep = set(best_comp)
    dropped = len(adj) - len(keep)
    if dropped:
        log(f"  {dropped} node di luar jaringan utama diabaikan (tidak terhubung).")
    osm_ids = sorted(keep)
    index = {nid: i for i, nid in enumerate(osm_ids)}
    lat = [osm_nodes[n][0] for n in osm_ids]
    lon = [osm_nodes[n][1] for n in osm_ids]

    eu: List[int] = []
    ev: List[int] = []
    elen: List[float] = []
    ekind: List[str] = []
    eflag: List[str] = []
    for (a, b), kind in edge_kind.items():
        if a in keep and b in keep:
            ia, ib = index[a], index[b]
            eu.append(ia)
            ev.append(ib)
            elen.append(round(haversine_m(lat[ia], lon[ia], lat[ib], lon[ib]), 2))
            ekind.append(kind)
            eflag.append(edge_flag.get((a, b), ""))

    lat0 = sum(lat) / len(lat)
    lon0 = sum(lon) / len(lon)
    proj = LocalProjection(lat0, lon0)

    # ---------- 2b. Geometri bangunan: way, multipolygon, building:part ----------
    bgeo = _building_geometry(buildings_raw, parts_raw, proj, p, log)

    # ---------- 3. Elevasi: simpul jalan + titik tengah setiap bangunan (satu panggilan DEM) ----------
    log(f"  Mengambil elevasi untuk {len(lat)} node + {len(bgeo)} bangunan ...")
    vals = list(elevation(list(zip(lat, lon)) + [(g["clat"], g["clon"]) for g in bgeo]))
    elev = [float(e) for e in vals[:len(lat)]]
    belev = [_finite(v) for v in vals[len(lat):]] if len(vals) >= len(lat) + len(bgeo) else [None] * len(bgeo)

    # ---------- 4. Jarak ke garis pantai ----------
    coast_grid = SegmentGrid(cell_m=250.0)
    for line in coast_lines:
        pts = [proj.to_xy(a, b) for a, b in line]
        for (ax, ay), (bx, by) in zip(pts[:-1], pts[1:]):
            coast_grid.add(ax, ay, bx, by, None)
    coast_dist: List[float] = []
    for i in range(len(lat)):
        x, y = proj.to_xy(lat[i], lon[i])
        hit = coast_grid.nearest(x, y, max_radius_m=p["hazard_max_coast_dist_m"] + 2000)
        coast_dist.append(round(hit[0], 1) if hit else 1e9)

    # ---------- 5. Zona bahaya dan target aman ----------
    polys_xy = None
    if hazard_polygons:
        polys_xy = [[proj.to_xy(la, lo) for (lo, la) in ring] for ring in hazard_polygons]
    thr = p["hazard_elev_threshold_m"]
    dmax = p["hazard_max_coast_dist_m"]
    if polys_xy is not None:
        hazard, safe_target = _hazard_from_polygons([proj.to_xy(lat[i], lon[i]) for i in range(len(lat))],
                                                    polys_xy, p["hazard_edge_buffer_m"])
    else:
        hazard, safe_target = [], []
        margin = max(p["safe_buffer_m"], p.get("dem_uncertainty_m", 0.0))
        for i in range(len(lat)):
            hz = elev[i] < thr and coast_dist[i] < dmax
            hazard.append(1 if hz else 0)
            ok = (not hz) and (elev[i] >= thr + margin or coast_dist[i] >= dmax)
            safe_target.append(1 if ok else 0)
    if not any(safe_target):
        log("  PERINGATAN: tidak ada node aman di area ini. Perluas batas area ke arah daratan tinggi.")

    # ---------- 6. Bangunan ----------
    edge_grid = SegmentGrid(cell_m=80.0)
    nxy = [proj.to_xy(lat[i], lon[i]) for i in range(len(lat))]
    for e in range(len(eu)):
        ax, ay = nxy[eu[e]]
        bx, by = nxy[ev[e]]
        edge_grid.add(ax, ay, bx, by, e)
    buildings: List[dict] = []
    for g, dem_b in zip(bgeo, belev):
        cx, cy = g["cx"], g["cy"]
        hit = edge_grid.nearest(cx, cy, max_radius_m=600)
        if hit is None:
            continue
        d, t, _, _, e = hit
        tags = g["tags"]
        levels, levels_src, height_m, levels_est, levels_note = g["levels"]
        kind = _building_kind(tags, levels)
        structure = structure_from_tags(tags)
        area_m2 = g["area"]
        # Penduduk: aturan lama (lihat RESIDENTIAL_BUILDING), tidak bergantung pada klasifikasi shelter.
        residents = p["persons_per_house"] if _legacy_dwelling(tags) else 0.0
        if residents and tags.get("building") == "apartments":
            residents = p["persons_per_house"] * max(1, levels) * max(1.0, area_m2 / 120.0)
        u, v = eu[e], ev[e]
        road_elev = _finite(elev[u] + t * (elev[v] - elev[u]))
        ev_b = building_elevation(dem_b, road_elev, d, p)  # DEM di gedung diutamakan; lihat building_elevation
        b_elev = ev_b["elev"]
        if polys_xy is not None:
            b_hazard = int(any(point_in_polygon(cx, cy, ring) for ring in polys_xy))
        else:
            ch = coast_grid.nearest(cx, cy, max_radius_m=dmax + 2000)
            b_coast = ch[0] if ch else 1e9
            b_hazard = int((b_elev is None or b_elev < thr) and b_coast < dmax)
        rec = {
            "id": g["id"],
            "name": tags.get("name", ""),
            "kind": kind,
            "btag": str(tags.get("building", ""))[:40],
            # Alasan Tidak Aman dari tag OSM (kategori/siklus hidup/kondisi); "" = tidak ada. Lihat tsunami.eligible.
            "osm_unsafe": osm_unsafe_reason(tags),
            "levels": levels,
            "levels_src": levels_src,
            "levels_est": levels_est,
            "height_m": round(height_m, 1) if height_m is not None else None,
            "basement": _underground(tags),
            "structure": structure,
            "area_m2": round(area_m2, 1),
            "lat": round(g["clat"], 7), "lon": round(g["clon"], 7),
            "poly": [[round(c, 7), round(a, 7)] for a, c in g["ring"]],
            "geom": g["geom"],
            "residents": round(residents, 2),
            "edge": e, "t": round(t, 4), "perp_m": round(d, 1),
            **ev_b,
            "hazard": b_hazard,
        }
        if levels_note:
            rec["levels_note"] = levels_note
        conflict = _levels_conflict(tags, p)
        if conflict:
            rec["levels_conflict"] = conflict
        if g.get("cap_area"):
            rec["cap_area_m2"] = round(g["cap_area"], 1)  # luas bagian tertinggi (building:part) untuk kapasitas
        ml = g.get("min_level", parse_levels(tags.get("building:min_level")) or 0)
        if ml:
            rec["min_level"] = int(ml)
        refresh_flags(rec, p)
        buildings.append(rec)

    n_people = sum(b["residents"] for b in buildings)
    log(f"  Graf: {len(lat)} node, {len(eu)} ruas | bangunan: {len(buildings)} | estimasi penduduk: {n_people:.0f}")

    bbox = [min(lon), min(lat), max(lon), max(lat)]
    area = {
        "meta": {
            "id": area_id,
            "name": name,
            "schema": AREA_SCHEMA,
            "synthetic": synthetic,
            "created": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "bbox": bbox,
            "center": [lon0, lat0],
            "sources": sources or [],
            "hazard_method": "polygon" if hazard_polygons else "rule",
            # Provenance: kapan data mentah diambil. osm_base = cap waktu basis data Overpass (bila ada).
            "snapshot": {"built_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                         "osm_base": (overpass.get("osm3s") or {}).get("timestamp_osm_base"),
                         "elevation": getattr(elevation, "used", None) or getattr(elevation, "name", None),
                         "synthetic": synthetic},
            "counts": {"nodes": len(lat), "edges": len(eu), "buildings": len(buildings),
                       "people": round(n_people)},
        },
        "params": p,
        "nodes": {
            "lat": [round(v, 7) for v in lat],
            "lon": [round(v, 7) for v in lon],
            "elev": [round(v, 1) for v in elev],
            "coast": coast_dist,
            "hazard": hazard,
            "safe": safe_target,
        },
        "edges": {"u": eu, "v": ev, "len": elen, "kind": ekind, "flag": eflag},
        "buildings": buildings,
        "coastline": [[[round(c, 7), round(a, 7)] for a, c in line] for line in coast_lines],
        "water": [[[round(c, 7), round(a, 7)] for a, c in line] for line in water_lines],
        "official_points": official_points or [],
        "display": display or {},
    }
    if hazard_polygons:
        area["hazard_polygons"] = [[[round(lo, 7), round(la, 7)] for lo, la in ring] for ring in hazard_polygons]
    return area


def _hazard_from_polygons(nxy: List[Tuple[float, float]], polys_xy: List[List[Tuple[float, float]]],
                          buffer_m: float) -> Tuple[List[int], List[int]]:
    """Zona bahaya dari poligon resmi; target aman = di luar poligon DAN minimal buffer_m dari batasnya."""
    border = SegmentGrid(cell_m=100.0)
    for ring in polys_xy:
        for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1]):
            border.add(ax, ay, bx, by, None)
    hazard, safe = [], []
    for x, y in nxy:
        hz = any(point_in_polygon(x, y, ring) for ring in polys_xy)
        hazard.append(1 if hz else 0)
        if hz:
            safe.append(0)
        else:
            hit = border.nearest(x, y, max_radius_m=buffer_m)
            safe.append(0 if hit is not None and hit[0] < buffer_m else 1)
    return hazard, safe


def apply_hazard_polygons(area: dict, rings: List[List[Tuple[float, float]]], source: str) -> dict:
    """Ganti zona bahaya area dengan poligon peta bahaya resmi (ring [(lon, lat), ...])."""
    if not rings:
        raise ValueError("Tidak ada poligon.")
    proj = LocalProjection(area["meta"]["center"][1], area["meta"]["center"][0])
    N = area["nodes"]
    polys_xy = [[proj.to_xy(la, lo) for (lo, la) in ring] for ring in rings]
    nxy = [proj.to_xy(a, b) for a, b in zip(N["lat"], N["lon"])]
    N["hazard"], N["safe"] = _hazard_from_polygons(nxy, polys_xy, float(area["params"].get("hazard_edge_buffer_m", 50)))
    for b in area["buildings"]:
        x, y = proj.to_xy(b["lat"], b["lon"])
        b["hazard"] = int(any(point_in_polygon(x, y, ring) for ring in polys_xy))
    area["hazard_polygons"] = [[[round(lo, 7), round(la, 7)] for lo, la in ring] for ring in rings]
    meta = area["meta"]
    meta["hazard_method"] = "polygon"
    meta["sources"] = [x for x in meta.get("sources", []) if not x.startswith("Zona bahaya:")] + [f"Zona bahaya: {source}"]
    return {"hazard_nodes": sum(N["hazard"]), "safe_nodes": sum(N["safe"]), "nodes": len(N["hazard"])}


def validate_area(area: dict) -> List[str]:
    """Pemeriksaan kewajaran data area; mengembalikan daftar peringatan."""
    warns = []
    n = len(area["nodes"]["lat"])
    for key in ("lon", "elev", "hazard", "safe", "coast"):
        if len(area["nodes"][key]) != n:
            warns.append(f"Panjang kolom nodes.{key} tidak sama dengan jumlah node.")
    m = len(area["edges"]["u"])
    for key in ("v", "len", "kind"):
        if len(area["edges"][key]) != m:
            warns.append(f"Panjang kolom edges.{key} tidak sama dengan jumlah ruas.")
    if not any(area["nodes"]["safe"]):
        warns.append("Tidak ada node aman.")
    long_edges = sum(1 for L in area["edges"]["len"] if L > 2000)
    if long_edges:
        warns.append(f"{long_edges} ruas lebih dari 2 km (periksa data).")
    if math.isnan(sum(area["nodes"]["elev"])):
        warns.append("Ada elevasi NaN.")
    return warns
