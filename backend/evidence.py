"""Tsunami Ready Evidence Pack: ringkasan bukti PENDUKUNG per indikator UNESCO-IOC Tsunami Ready.

SEMPAT bukan sistem peringatan dini resmi dan tidak menilai status Tsunami Ready. Paket ini merangkum data dan
hasil model yang bisa dipakai BPBD/komunitas sebagai bahan, lengkap dengan sumber, versi model, dan keterbatasan.
Indikator yang memerlukan keputusan/kegiatan lembaga (pengesahan peta, rambu, sosialisasi, latihan, rencana,
penerimaan peringatan resmi) ditandai "di luar cakupan SEMPAT" atau "perlu pengesahan".

Daftar 12 indikator: UNESCO/IOC Tsunami Ready Recognition Programme (IOC Manuals and Guides 74).
"""
from __future__ import annotations

import json
import math
from typing import Dict, List, Optional

from . import __version__, tsunami
from .speed import GROUP_SPEED_MPS

# Status bukti (bukan status kepatuhan):
TERSEDIA = "bukti pendukung tersedia"
PERKIRAAN = "perkiraan model, perlu data/validasi resmi"
SAHKAN = "draf tersedia, perlu pengesahan lembaga"
LUAR = "di luar cakupan SEMPAT"
BELUM = "belum ada data terverifikasi"
SINTETIS = "contoh dari data SINTETIS (bukan bukti)"

INDICATORS = [
    ("ASSESS-1", "Zona bahaya tsunami dipetakan dan ditetapkan."),
    ("ASSESS-2", "Jumlah orang berisiko di zona bahaya diperkirakan."),
    ("ASSESS-3", "Sumber daya ekonomi, infrastruktur, politik, dan sosial diidentifikasi."),
    ("PREP-1", "Peta evakuasi tsunami yang mudah dipahami disahkan."),
    ("PREP-2", "Informasi tsunami termasuk rambu dipasang di ruang publik."),
    ("PREP-3", "Bahan penyadaran dan edukasi publik tersedia dan dibagikan."),
    ("PREP-4", "Kegiatan sosialisasi/edukasi diadakan minimal tiga kali setahun."),
    ("PREP-5", "Latihan tsunami komunitas diadakan minimal setiap dua tahun."),
    ("RESP-1", "Rencana tanggap darurat tsunami komunitas disahkan."),
    ("RESP-2", "Kapasitas mengelola operasi tanggap darurat saat tsunami tersedia."),
    ("RESP-3", "Sarana yang andal dan berlapis untuk menerima peringatan resmi 24 jam tersedia."),
    ("RESP-4", "Sarana yang andal dan berlapis untuk menyebarkan peringatan resmi 24 jam ke publik tersedia."),
]


def _audit(store, aid: str, actions: List[str], limit: int = 50) -> List[dict]:
    marks = ",".join("?" * len(actions))
    rows = store._q(f"SELECT at, user, action, detail FROM audit WHERE action IN ({marks}) ORDER BY id DESC LIMIT 2000",
                    tuple(actions))
    out = []
    for r in rows:
        try:
            d = json.loads(r["detail"] or "{}")
        except ValueError:
            d = {}
        if d.get("area") == aid:
            out.append({"at": r["at"], "user": r["user"], "action": r["action"], "detail": d})
        if len(out) >= limit:
            break
    return out


def build(c, st, *, eta_min: float, wave_m: Optional[float], delay_min: float, margin_min: float) -> dict:
    eng = c.engine
    area = c.area
    meta = area["meta"]
    p = area["params"]
    shelters = st.shelter_ids(c)
    plan = eng.plan(eta_min=eta_min, delay_min=delay_min, margin_min=margin_min, shelter_ids=shelters,
                    closures=st.closures(c), optimize_k=0, design_height_m=wave_m, at=tsunami.WORST_CASE)
    H = plan["wave_m"]
    B = eng.buildings
    dem = eng.demand()
    pop = eng.demand_info()

    # ASSESS-2: orang di zona bahaya (bangunan/asal di simpul zona bahaya), per kelompok.
    in_hz = {"umum": 0, "rentan": 0}
    total = {"umum": 0, "rentan": 0}
    for bi, q in dem:
        for g in q:
            total[g] += q[g]
            if B[bi].get("hazard"):
                in_hz[g] += q[g]

    # PREP-1 (bahan peta): sebaran waktu tempuh alami ke zona aman per pita 3 menit (orang), per kelompok.
    band_s = 180.0
    bands = {g: {} for g in ("umum", "rentan")}
    no_route = {"umum": 0, "rentan": 0}
    for g in bands:
        times = plan["building_time"][g]
        for bi, q in dem:
            n = q.get(g, 0)
            if not n:
                continue
            t = times[bi]
            if t is None:
                no_route[g] += n
                continue
            k = min(10, int(t // band_s))
            bands[g][k] = bands[g].get(k, 0) + n

    # ASSESS-3: sumber daya.
    tiers: Dict[str, int] = {}
    tall_tiers: Dict[str, int] = {}
    for b in B:
        if b.get("virtual"):
            continue
        t = b.get("tier") or "unverified"
        tiers[t] = tiers.get(t, 0) + 1
        if int(b.get("levels") or 1) >= 2:
            tall_tiers[t] = tall_tiers.get(t, 0) + 1
    dest = []
    for bid in plan["destination_ids"]:
        b = B[eng.bindex[bid]]
        a = tsunami.assess(b, H, p, at=tsunami.WORST_CASE)
        dest.append({"id": bid, "name": b.get("name") or f"Gedung {b.get('levels')} lantai", "kind": b.get("kind"),
                     "levels": b.get("levels"), "min_floor": a["min_floor"], "min_floor_nominal": a["min_floor_nominal"],
                     "capacity": a["capacity"], "registered": bid in shelters, "lat": b["lat"], "lon": b["lon"],
                     "surveyed_at": (b.get("survey") or {}).get("at")})
    dest.sort(key=lambda d: -d["capacity"])
    points = st.points_for(c)
    surveys = st.store.list_building_info(c.id)

    # PREP-5 / RESP: catatan simulasi & survei dari audit log (bukti kegiatan yang tercatat di SEMPAT saja).
    sims = _audit(st.store, c.id, ["simulate_alert"], 20)
    survey_log = _audit(st.store, c.id, ["survey_building", "clear_survey"], 200)

    hz = area.get("hazard_view") or {}
    hazard = {"method": meta.get("hazard_method", "rule"), "official": bool(hz.get("official")), "label": hz.get("label"),
              "rule": None if hz.get("official") else {"elev_m": p.get("hazard_elev_threshold_m"),
                                                       "coast_km": (p.get("hazard_max_coast_dist_m") or 0) / 1000}}
    budget = plan["budget_s"]
    status = {
        "ASSESS-1": (TERSEDIA if hazard["official"] else PERKIRAAN,
                     hazard["label"] or "",
                     "Zona resmi dari unggahan peta bahaya." if hazard["official"] else
                     "Zona dari aturan elevasi dan jarak pantai, BUKAN penetapan resmi. Unggah peta bahaya resmi (InaRISK/BPBD)."),
        "ASSESS-2": (TERSEDIA if pop["method"] == "grid" and not pop["synthetic"] else PERKIRAAN,
                     f"{sum(in_hz.values()):,} dari {sum(total.values()):,} orang di zona bahaya ({pop.get('label') or pop['method']}).".replace(",", "."),
                     "Grid penduduk diunggah." if pop["method"] == "grid" else "Model orang per rumah, bukan data kependudukan."),
        "ASSESS-3": (TERSEDIA if (dest or shelters) else BELUM, f"{len(dest)} gedung evakuasi vertikal terverifikasi (kapasitas {sum(d['capacity'] for d in dest):,} orang), "
                     f"{len(shelters)} shelter resmi terdaftar, {len(points)} titik bantuan, {len(area.get('facilities') or [])} fasilitas OSM.".replace(",", "."),
                     "Inventaris gedung & fasilitas; sumber daya ekonomi/politik/sosial lain tidak dicakup."),
        "PREP-1": (SAHKAN, f"Peta draf: zona bahaya, zona aman, gedung terverifikasi, waktu tempuh. Anggaran jalan {budget / 60:g} menit.",
                   "SEMPAT menyusun bahan peta; pengesahan dan desain peta cetak oleh BPBD."),
        "PREP-2": (LUAR, "", "Rambu fisik tidak dicatat di SEMPAT."),
        "PREP-3": (LUAR, "Panduan cetak per titik tersedia di halaman perencana.", "Distribusi bahan edukasi tidak dicatat."),
        "PREP-4": (LUAR, "", "Kegiatan sosialisasi tidak dicatat."),
        "PREP-5": (TERSEDIA if sims else LUAR, f"{len(sims)} simulasi peringatan tercatat di SEMPAT." if sims else "",
                   "Simulasi di aplikasi bukan pengganti latihan evakuasi lapangan."),
        "RESP-1": (SAHKAN, "Bahan rencana: alokasi kapasitas, daftar verifikasi gedung, rekomendasi gedung tambahan.",
                   "Rencana tanggap darurat disusun dan disahkan lembaga."),
        "RESP-2": (LUAR, "Dasbor petugas (titik bantuan, jalan putus, kapasitas) mendukung operasi.", "Kapasitas organisasi tidak dinilai."),
        "RESP-3": (LUAR, "SEMPAT membaca info gempa BMKG bila diaktifkan (tidak menggantikan kanal resmi).",
                   "Penerimaan peringatan resmi 24 jam harus melalui kanal BMKG/BPBD."),
        "RESP-4": (LUAR, "Notifikasi web push & SSE adalah SATU kanal tambahan, bukan sarana berlapis resmi.",
                   "Sirine, radio, pengeras suara masjid, SMS operator, dll. di luar SEMPAT."),
    }
    synth = bool(meta.get("synthetic"))
    indicators = [{"code": k, "text": t, "evidence": status[k][1], "note": status[k][2],
                   # Area sintetis tidak pernah menghasilkan "bukti": hanya contoh tampilan.
                   "status": SINTETIS if synth and status[k][0] in (TERSEDIA, PERKIRAAN, SAHKAN) else status[k][0]}
                  for k, t in INDICATORS]

    return {
        "generated_version": __version__,
        "disclaimer": ("Dokumen pendukung, BUKAN penilaian atau pernyataan status Tsunami Ready, dan SEMPAT bukan sistem "
                       "peringatan dini resmi. Peringatan resmi: BMKG. Semua angka adalah hasil model dengan keterbatasan "
                       "di bagian akhir dokumen."),
        "area": {"id": c.id, "name": meta.get("name"), "synthetic": bool(meta.get("synthetic")), "bbox": meta.get("bbox"),
                 "center": meta.get("center"), "sources": meta.get("sources") or [], "timezone": (c.tz or {}).get("label")},
        "model": eng.model_info(),
        "scenario": {"eta_min": eta_min, "delay_min": delay_min, "margin_min": margin_min, "budget_s": budget,
                     "wave_m": H, "time": plan["time"], "speeds_mps": dict(GROUP_SPEED_MPS)},
        "indicators": indicators,
        "hazard": hazard,
        "population": {"method": pop["method"], "label": pop.get("label"), "synthetic": pop["synthetic"],
                       "total": total, "in_hazard": in_hz, "virtual_origins": pop.get("virtual_origins", 0)},
        "time_bands": {"band_s": band_s, "people": {g: {str(k): v for k, v in sorted(bands[g].items())} for g in bands},
                       "no_route": no_route},
        "reach": {"total": plan["total_people"], "late_natural": plan["red_before"],
                  "late_with_registered": plan["red_after_registered"],
                  "allocation": {k: plan["allocation"][k] for k in ("status", "method", "optimal")}},
        "buildings": {"total": sum(tiers.values()), "tiers": tiers, "tall_tiers": tall_tiers,
                      "destinations": dest[:60], "destination_count": len(dest),
                      "verification": [dict(v, missing=list(B[eng.bindex[v["id"]]].get("tier_reasons") or []))
                                       for v in plan["verification_list"][:40]],
                      "verification_count": plan["verification_count"],
                      "unknown_levels": plan["unknown_levels"], "surveyed": len(surveys)},
        "shelters": plan["shelters"],
        "points": [{"name": x.get("name"), "kind": x.get("kind"), "status": x.get("status"), "capacity": x.get("capacity")}
                   for x in points][:40],
        "activity": {"simulations": sims, "survey_count": len([x for x in survey_log if x["action"] == "survey_building"]),
                     "first_survey": survey_log[-1]["at"] if survey_log else None,
                     "last_survey": survey_log[0]["at"] if survey_log else None},
        "policy": {
            "tiers": "Terverifikasi = semua syarat survei terpenuhi (lantai dihitung di lokasi, struktur beton/baja, kondisi baik, "
                     "akses diketahui, elevasi tidak kosong) dan lantai aman konservatif tersedia. Hanya gedung terverifikasi "
                     "yang menjadi tujuan alokasi. Perlu Verifikasi = tampil sebagai info tanpa rute. Tidak Aman = tidak pernah tujuan.",
            "elev_margin_m": dict(p.get("elev_margin_m") or tsunami.DEFAULT_ELEV_MARGIN_M),
            "depth": "Kedalaman konservatif = H - (elevasi - margin kepercayaan elevasi); lantai aman = ceil(kedalaman / tinggi lantai) + 1.",
            "floor_height_m": tsunami.floor_height_m(p),
            "deadline": "Batas tiba = waktu peringatan + ETA - margin; anggaran jalan alokasi = ETA - jeda mulai - margin.",
            "note": "Margin elevasi adalah parameter konservatif awal dan perlu divalidasi BPBD atau data elevasi lebih akurat.",
        },
        "limitations": [
            "Jalan dari OpenStreetMap: jalan setapak yang belum dipetakan tidak dihitung; bangunan dan pagar tidak dimodelkan sebagai penghalang.",
            "Elevasi DEM (resolusi 30 sampai 90 m) punya galat beberapa meter; karena itu dipakai margin konservatif.",
            "Kecepatan jalan kaki memakai nilai kelompok tetap; kemacetan dan kepanikan tidak dimodelkan.",
            "Penduduk: " + (pop.get("label") or pop["method"]) + ".",
            "Grid waktu 30 m adalah resolusi tampilan, bukan akurasi 30 m.",
            "Rekomendasi gedung tambahan memakai heuristik greedy; angka akhirnya dihitung ulang dengan alokasi LP.",
        ],
    }
