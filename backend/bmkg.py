"""Pembacaan data gempa terbuka BMKG (autogempa.json).

Atribusi wajib: setiap tampilan data menyebut "Sumber: BMKG".
SEMPAT tidak menggantikan peringatan resmi; data ini dipakai untuk memicu
panduan evakuasi di aplikasi.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Optional

from .geo import haversine_m


def _num(text: str) -> Optional[float]:
    if text is None:
        return None
    s = "".join(ch for ch in str(text).replace(",", ".") if ch.isdigit() or ch in ".-")
    try:
        return float(s)
    except ValueError:
        return None


def parse_autogempa(doc: dict) -> Optional[dict]:
    """Ubah JSON autogempa BMKG menjadi kejadian SEMPAT. None bila formatnya tidak dikenal."""
    g = (doc or {}).get("Infogempa", {}).get("gempa")
    if not isinstance(g, dict):
        return None
    lat = lon = None
    coords = g.get("Coordinates")
    if coords and "," in coords:
        a, b = coords.split(",", 1)
        lat, lon = _num(a), _num(b)
    if lat is None:
        lat = _num(g.get("Lintang"))
        if lat is not None and "LS" in str(g.get("Lintang", "")).upper():
            lat = -abs(lat)
        lon = _num(g.get("Bujur"))
    potensi = str(g.get("Potensi", ""))
    tsunami = "tsunami" in potensi.lower() and "tidak" not in potensi.lower()
    return {
        "key": g.get("DateTime") or f"{g.get('Tanggal')} {g.get('Jam')}",
        "issued_at": g.get("DateTime"),
        "magnitude": _num(g.get("Magnitude")),
        "depth_km": _num(g.get("Kedalaman")),
        "lat": lat, "lon": lon,
        "region": g.get("Wilayah", ""),
        "potensi": potensi,
        "tsunami": tsunami,
        "local_time": f"{g.get('Tanggal', '')} {g.get('Jam', '')}".strip(),
    }


def fetch(url: str, timeout: float = 15.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "SEMPAT/0.1 (hackathon; atribusi BMKG)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def relevant(event: dict, center_lat: float, center_lon: float, radius_km: float) -> bool:
    if not event or not event.get("tsunami") or event.get("lat") is None:
        return False
    return haversine_m(event["lat"], event["lon"], center_lat, center_lon) <= radius_km * 1000
