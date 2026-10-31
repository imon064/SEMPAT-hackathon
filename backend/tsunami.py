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
from typing import Optional, Tuple

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


def climb_to_floor_s(floor: int) -> float:
    return max(0, floor - 1) * FLOOR_HEIGHT_M / STAIR_VERTICAL_MPS


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


def eligible(b: dict) -> Tuple[bool, str]:
    """Apakah bangunan boleh dipertimbangkan sebagai tempat evakuasi vertikal (sebelum melihat tinggi air)."""
    if b.get("levels_src", "default") == "default":
        return False, "Jumlah lantai tidak diketahui"
    if b.get("kind") == "rumah" and b.get("levels_src") != "survey":
        return False, "Rumah tinggal (bukan fasilitas umum)"
    if b.get("structure") in WEAK_STRUCTURES:
        return False, "Struktur bukan beton/baja"
    if b.get("access") == "tidak":
        return False, "Lantai atas tidak dapat diakses umum"
    return True, ""


def assess(b: dict, wave_m: float, params: dict) -> dict:
    """Kelayakan satu gedung sebagai tempat evakuasi vertikal untuk tinggi tsunami tertentu."""
    levels = int(b.get("levels") or 1)
    depth = depth_at(b.get("elev", 0.0), wave_m)
    floor = min_safe_floor(depth)
    usable = max(0, levels - floor + 1) if depth > 0 else levels
    factor = float(params.get("shelter_usable_factor", 0.5))
    per_person = float(params.get("space_per_person_m2", 0.93))
    capacity = int(b.get("area_m2", 0) * usable * factor / per_person) if usable > 0 else 0
    ok, why = eligible(b)
    if ok and depth <= 0:
        ok, why = False, "Tanah lebih tinggi dari perkiraan tsunami ini — tidak perlu naik gedung"
    elif ok and levels < floor:
        ok, why = False, f"Terlalu rendah: perlu lantai {floor} ke atas, gedung {levels} lantai"
    return {"depth_m": round(depth, 1), "min_floor": floor, "usable_floors": usable,
            "capacity": capacity, "climb_s": round(climb_to_floor_s(floor), 1),
            "suitable": ok, "reason": why}


def registered_ok(b: dict, a: dict) -> bool:
    """Shelter yang ditetapkan perencana tetap dipakai selama lantainya cukup tinggi untuk skenario ini."""
    return int(b.get("levels") or 1) >= a["min_floor"] and b.get("access") != "tidak"
