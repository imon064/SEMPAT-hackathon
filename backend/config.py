"""Konfigurasi lewat variabel lingkungan (semua opsional)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SEMPAT_DATA_DIR", ROOT / "data"))
AREAS_DIR = DATA_DIR / "areas"
FRONTEND_DIR = ROOT / "frontend"
DB_PATH = DATA_DIR / "sempat.db"
USERS_PATH = DATA_DIR / "users.json"
SECRET_PATH = DATA_DIR / "secret.key"

AREA_ID = os.environ.get("SEMPAT_AREA", "")  # kosong = area pertama yang ditemukan
HOST = os.environ.get("SEMPAT_HOST", "127.0.0.1")
PORT = int(os.environ.get("SEMPAT_PORT") or os.environ.get("PORT") or "8000")
# Alamat yang bisa dibuka HP warga (dipakai untuk kode QR). Diisi otomatis oleh run.py bila --host 0.0.0.0.
PUBLIC_URL = (os.environ.get("SEMPAT_PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")


def is_public() -> bool:
    """Server dapat dibuka dari internet: alamat https (tunnel Cloudflare/Render), atau SEMPAT_PUBLIC=1 (Docker).

    Dibaca saat dipanggil (bukan saat impor) karena run.py mengisi PUBLIC_URL setelah tunnel terbentuk.
    Alamat LAN http://192.168.x.x tidak dianggap publik.
    Di server publik, akun demo (petugas123/perencana123) tidak dibuat dan tidak bisa dipakai.
    """
    return (os.environ.get("SEMPAT_PUBLIC") == "1" or bool(os.environ.get("RENDER_EXTERNAL_URL"))
            or PUBLIC_URL.startswith("https://"))


# Ambil data gempa terbuka BMKG secara berkala (butuh internet). 0 = mati.
BMKG_POLL = os.environ.get("SEMPAT_BMKG_POLL", "1") == "1"
BMKG_URL = os.environ.get("SEMPAT_BMKG_URL", "https://data.bmkg.go.id/DataMKG/TEWS/autogempa.json")
BMKG_INTERVAL_S = int(os.environ.get("SEMPAT_BMKG_INTERVAL", "60"))
BMKG_RADIUS_KM = float(os.environ.get("SEMPAT_BMKG_RADIUS_KM", "600"))      # peringatan tsunami
BMKG_INFO_RADIUS_KM = float(os.environ.get("SEMPAT_BMKG_INFO_RADIUS_KM", "300"))  # notifikasi info gempa
ALERT_MAX_HOURS = float(os.environ.get("SEMPAT_ALERT_MAX_HOURS", "3"))  # peringatan otomatis berakhir

# Peta otomatis di sekitar posisi warga (butuh internet di server). 0 = mati.
LIVE_AREAS = os.environ.get("SEMPAT_LIVE_AREAS", "1") == "1"
MAX_LOADED_AREAS = int(os.environ.get("SEMPAT_MAX_LOADED_AREAS", "10"))
CACHE_DIR = DATA_DIR / "cache"

# Web Push: kunci VAPID dibuat otomatis di data/; untuk server cloud isi SEMPAT_VAPID_PRIVATE agar tetap.
VAPID_PRIVATE = os.environ.get("SEMPAT_VAPID_PRIVATE", "")
PUSH_CONTACT = os.environ.get("SEMPAT_PUSH_CONTACT", "mailto:sempat-demo@example.com")
# Pengiriman paralel: satu HP yang lambat/mati tidak boleh menunda peringatan ke HP lain.
PUSH_TIMEOUT_S = float(os.environ.get("SEMPAT_PUSH_TIMEOUT_S", "5"))   # per pelanggan
PUSH_WORKERS = int(os.environ.get("SEMPAT_PUSH_WORKERS", "32"))        # pengiriman bersamaan

# Kata sandi akun petugas/perencana (disarankan untuk server publik, min. 8 karakter):
#   SEMPAT_PETUGAS_PASSWORD, SEMPAT_PERENCANA_PASSWORD  — dibaca oleh auth.ensure_accounts().

# LLM opsional untuk panduan warga. Tanpa kunci, dipakai templat.
LLM_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.environ.get("SEMPAT_LLM_MODEL", "")

TOKEN_TTL_S = 12 * 3600

# Google Earth Engine (opsional) untuk tinggi gedung Google Open Buildings 2.5D.
# Isi dengan ID proyek Google Cloud yang sudah terdaftar di Earth Engine, lalu jalankan sekali:
#   pip install -r requirements-ee.txt  &&  earthengine authenticate
EE_PROJECT = os.environ.get("SEMPAT_EE_PROJECT", "")
EE_YEAR = int(os.environ.get("SEMPAT_EE_YEAR", "2023"))
EE_LIVE = os.environ.get("SEMPAT_EE_LIVE", "0") == "1"  # ikut ambil tinggi saat peta otomatis dibangun

# Alokasi kapasitas (latar belakang): batas waktu solver per tahap (detik).
ALLOC_TIME_LIMIT_S = float(os.environ.get("SEMPAT_ALLOC_TIME_LIMIT_S", "60"))
