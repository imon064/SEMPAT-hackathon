"""Jalankan SEMPAT.

    python run.py                    # server lokal (http://localhost:8000)
    python run.py --host 0.0.0.0     # bisa dibuka HP di Wi-Fi yang sama (tanpa GPS/notifikasi: bukan https)
    python run.py --tunnel           # + alamat https publik (Cloudflare) agar GPS & notifikasi di HP berfungsi
    python run.py --area tambakrejo  # pakai area desa tertentu sebagai bawaan
    python run.py --no-bmkg          # matikan pembacaan data gempa BMKG
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

if sys.version_info < (3, 10):
    sys.exit("SEMPAT membutuhkan Python 3.10 atau lebih baru.")


def ensure_synthetic(force: bool = False) -> Path:
    from backend import synthetic
    from backend.config import AREAS_DIR
    out = AREAS_DIR / synthetic.AREA_ID / "area.json"
    if force or not out.exists():
        print("Membangun desa sintetis untuk uji sistem ...")
        area = synthetic.build()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(area, separators=(",", ":")), encoding="utf-8")
        print(f"  Disimpan: {out}")
    return out


def lan_ip():
    """Alamat IP laptop di jaringan lokal (tanpa mengirim data keluar)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Tunnel https (Cloudflare Quick Tunnel, gratis, tanpa akun)
# ---------------------------------------------------------------------------
CF_URLS = {
    ("Windows", "AMD64"): ("cloudflared.exe", "cloudflared-windows-amd64.exe"),
    ("Windows", "x86_64"): ("cloudflared.exe", "cloudflared-windows-amd64.exe"),
    ("Linux", "x86_64"): ("cloudflared", "cloudflared-linux-amd64"),
    ("Linux", "aarch64"): ("cloudflared", "cloudflared-linux-arm64"),
}


def find_cloudflared() -> str | None:
    exe = shutil.which("cloudflared")
    if exe:
        return exe
    key = (platform.system(), platform.machine())
    local_name = CF_URLS.get(key, ("cloudflared", ""))[0]
    local = ROOT / "tools" / local_name
    if local.exists():
        return str(local)
    if key not in CF_URLS:
        return None
    url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/{CF_URLS[key][1]}"
    print(f"Mengunduh cloudflared (sekali saja, ± 50 MB) dari {url} ...")
    try:
        tmp = local.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {done * 100 // total}%", end="", flush=True)
        print()
        tmp.replace(local)
        if platform.system() != "Windows":
            local.chmod(0o755)
        return str(local)
    except Exception as exc:
        print(f"  Gagal mengunduh cloudflared: {exc}")
        return None


def start_tunnel(port: int):
    exe = find_cloudflared()
    if not exe:
        print("Tunnel https tidak tersedia. Pasang cloudflared: https://developers.cloudflare.com/cloudflare-one/"
              "connections/connect-networks/downloads/")
        return None, None
    proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{port}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                            encoding="utf-8", errors="replace")
    url = None
    deadline = time.time() + 45
    for line in proc.stdout:  # type: ignore[union-attr]
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if m:
            url = m.group(0)
            break
        if time.time() > deadline or proc.poll() is not None:
            break
    # terus kuras keluaran agar proses tidak macet
    threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True).start()  # type: ignore[union-attr]
    return proc, url


def main() -> None:
    ap = argparse.ArgumentParser(description="Server SEMPAT")
    ap.add_argument("--area", default=os.environ.get("SEMPAT_AREA", ""), help="ID area bawaan di data/areas/")
    ap.add_argument("--host", default=os.environ.get("SEMPAT_HOST", "127.0.0.1"),
                    help="0.0.0.0 agar bisa dibuka dari HP di jaringan yang sama")
    ap.add_argument("--port", type=int, default=int(os.environ.get("SEMPAT_PORT") or os.environ.get("PORT") or 8000))
    ap.add_argument("--tunnel", action="store_true", help="Buat alamat https publik (Cloudflare) untuk HP & juri")
    ap.add_argument("--no-bmkg", action="store_true", help="Matikan pembacaan data gempa BMKG")
    ap.add_argument("--bmkg", action="store_true", help=argparse.SUPPRESS)  # kompatibilitas lama (sekarang bawaan)
    ap.add_argument("--rebuild-synthetic", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    os.environ["SEMPAT_AREA"] = args.area
    os.environ["SEMPAT_BMKG_POLL"] = "0" if args.no_bmkg else os.environ.get("SEMPAT_BMKG_POLL", "1")

    ensure_synthetic(force=args.rebuild_synthetic)

    try:
        import uvicorn  # noqa: F401
        import cryptography  # noqa: F401
    except ImportError:
        sys.exit("Paket belum terpasang. Jalankan: pip install -r requirements.txt")

    import backend.config as cfg
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    lan = lan_ip() if args.host == "0.0.0.0" else None
    tunnel_proc = None
    if args.tunnel:
        tunnel_proc, turl = start_tunnel(args.port)
        if turl:
            cfg.PUBLIC_URL = turl
    if not cfg.PUBLIC_URL and lan:
        cfg.PUBLIC_URL = f"http://{lan}:{args.port}"

    print("\nSEMPAT berjalan:")
    print(f"  Aplikasi warga   : {url}/")
    print(f"  Aplikasi petugas : {url}/petugas   (akun demo: petugas / petugas123)")
    print(f"  Dasbor perencana : {url}/perencana (akun demo: perencana / perencana123)")
    if cfg.PUBLIC_URL.startswith("https://"):
        print("\n  ==========================================================")
        print(f"  ALAMAT UNTUK HP / JURI (https): {cfg.PUBLIC_URL}/")
        print("  GPS dan notifikasi di HP berfungsi lewat alamat ini.")
        print("  Kode QR-nya ada di halaman petugas (kartu 'Uji & demo').")
        print("  ==========================================================\n")
    elif lan:
        print(f"  Dari HP (Wi-Fi sama): http://{lan}:{args.port}/  (tanpa GPS/notifikasi; pakai --tunnel untuk https)")
    print("  BMKG             : " + ("membaca data gempa terbuka tiap 60 detik" if cfg.BMKG_POLL else "mati"))
    print("  Ganti kata sandi : python -m backend.auth set <username> <kata_sandi_baru>")
    print("  Hentikan         : Ctrl+C\n")
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()

    import uvicorn
    try:
        uvicorn.run("backend.app:app", host=args.host, port=args.port, log_level="warning")
    finally:
        if tunnel_proc:
            tunnel_proc.terminate()


if __name__ == "__main__":
    main()
