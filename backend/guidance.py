"""Teks peringatan dan panduan warga.

Struktur pesan mengikuti riset komunikasi peringatan (Mileti & Sorensen, 1990):
sebutkan BAHAYA, LOKASI, TINDAKAN, WAKTU, dan SUMBER dengan bahasa yang
spesifik, konsisten, pasti, dan jelas.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Optional

from . import config


def alert_text(*, tsunami: bool, magnitude: Optional[float], region: str, eta_min: Optional[float],
               simulated: bool) -> dict:
    mag = f"M{magnitude:.1f}".replace(".", ",") if magnitude is not None else "Gempa"
    if tsunami:
        headline = "PERINGATAN DINI TSUNAMI"
        instruction = ("Segera menjauh dari pantai dan sungai, naik ke tempat tinggi atau lantai atas gedung kokoh. "
                       "Jangan menunggu air laut surut. Jangan kembali sampai peringatan dicabut.")
    else:
        headline = "INFO GEMPA"
        instruction = "Tetap waspada terhadap gempa susulan. Jauhi bangunan yang retak."
    when = (f"Perkiraan gelombang tiba ±{eta_min:.0f} menit setelah gempa." if tsunami and eta_min else "")
    source = "SIMULASI — bukan kejadian nyata" if simulated else "Sumber: BMKG"
    return {"headline": headline, "description": f"Gempa {mag} — {region}".strip(" —"),
            "instruction": instruction, "time_note": when, "source": source}


def _fmt_min(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    return f"{max(1, round(seconds / 60))} menit"


def template_guidance(info: dict, eta_min: float, place: str,
                      source_note: str = "data terbuka OSM, elevasi SRTM") -> str:
    u = info.get("umum", {})
    r = info.get("rentan", {})
    lines = [f"PANDUAN EVAKUASI TSUNAMI — {place}", ""]
    lines.append("Gempa kuat atau lama? Jangan tunggu sirene. Langsung pergi.")
    if u.get("already_safe"):
        lines.append("Titik ini sudah berada di zona aman. Tetap di sini sampai peringatan dicabut.")
    elif u.get("ok") and u.get("routes"):
        ru = u["routes"][0]
        dest = ru["dest"].get("name") or "zona aman"
        lines.append(f"Tujuan: {dest}. Jalan kaki ±{_fmt_min(ru['time_s'])} ({ru['distance_m']:.0f} m).")
        if r.get("ok") and r.get("routes"):
            lines.append(f"Bersama lansia/difabel: ±{_fmt_min(r['routes'][0]['time_s'])}. Berangkat paling awal.")
        lines.append(f"Perkiraan gelombang tiba ±{eta_min:.0f} menit setelah gempa.")
    else:
        lines.append("Tidak ditemukan rute ke tempat aman dari titik ini. Hubungi BPBD.")
    lines.append("Jauhi pantai dan sungai. Jangan kembali sampai peringatan dicabut.")
    lines.append(f"Sumber perhitungan: SEMPAT ({source_note}). Bukan pengganti peringatan resmi BMKG.")
    return "\n".join(lines)


def llm_rewrite(text: str) -> Optional[str]:
    """Haluskan bahasa panduan dengan LLM bila ANTHROPIC_API_KEY & SEMPAT_LLM_MODEL diisi.

    LLM hanya boleh menyusun ulang kalimat dari fakta yang diberikan, tidak menambah angka.
    """
    if not (config.LLM_API_KEY and config.LLM_MODEL):
        return None
    prompt = ("Susun ulang panduan evakuasi berikut agar mudah dipahami warga desa pesisir. "
              "Aturan: gunakan HANYA fakta dan angka yang ada, jangan menambah informasi baru, "
              "kalimat pendek, maksimal 7 baris, Bahasa Indonesia sederhana.\n\n" + text)
    body = json.dumps({"model": config.LLM_MODEL, "max_tokens": 600,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, method="POST", headers={
        "x-api-key": config.LLM_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            out = json.loads(resp.read().decode())
        parts = [c.get("text", "") for c in out.get("content", []) if c.get("type") == "text"]
        return "\n".join(parts).strip() or None
    except Exception:
        return None
