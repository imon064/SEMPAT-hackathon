"""Akun petugas/perencana dan token bertanda tangan (tanpa dependensi luar).

Kata sandi akun diatur lewat variabel lingkungan (disarankan untuk server publik):
    SEMPAT_PETUGAS_PASSWORD, SEMPAT_PERENCANA_PASSWORD   (minimal 8 karakter)

Akun demo (petugas/petugas123, perencana/perencana123) HANYA dibuat di server lokal/LAN.
Di server publik (tunnel https, Render, Docker; lihat config.is_public) akun demo tidak dibuat dan kata sandi
demo selalu ditolak. Bila variabel lingkungan tidak diisi, dibuat kata sandi acak yang dicetak SEKALI di konsol.

Ganti kata sandi manual:  python -m backend.auth set <username> <kata_sandi_baru>
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from typing import Callable, List, Optional, Tuple

from . import config

ITERATIONS = 200_000
ROLES = ("petugas", "perencana", "admin")
MIN_PASSWORD_LEN = 8

DEFAULT_USERS = [
    {"username": "petugas", "password": "petugas123", "role": "petugas", "name": "Petugas Lapangan (demo)"},
    {"username": "perencana", "password": "perencana123", "role": "perencana", "name": "Perencana BPBD (demo)"},
]
_DEFAULT_PW = {u["username"]: u["password"] for u in DEFAULT_USERS}


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS).hex()


def _set_pw(u: dict, password: str) -> None:
    salt = secrets.token_bytes(16)
    u["salt"], u["hash"] = salt.hex(), _hash(password, salt)


def _matches(u: dict, password: str) -> bool:
    return hmac.compare_digest(u["hash"], _hash(password, bytes.fromhex(u["salt"])))


def _env_password(username: str) -> str:
    return os.environ.get(f"SEMPAT_{username.upper()}_PASSWORD", "")


def _read() -> list:
    if not config.USERS_PATH.exists():
        return []
    return json.loads(config.USERS_PATH.read_text(encoding="utf-8"))


def _write(users: list) -> None:
    config.USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.USERS_PATH.write_text(json.dumps(users, indent=2), encoding="utf-8")


def ensure_accounts(log: Callable[[str], None] = print) -> List[Tuple[str, str]]:
    """Siapkan akun petugas & perencana. Aman dipanggil berulang kali.

    Urutan sumber kata sandi:
    1. Variabel lingkungan SEMPAT_<USERNAME>_PASSWORD (selalu diselaraskan ke users.json).
    2. Server publik: kata sandi acak; juga mengganti akun yang masih memakai kata sandi demo.
    3. Server lokal: akun demo (bila akun belum ada).
    Mengembalikan [(username, kata sandi acak)] yang baru dibuat (sudah dicetak lewat `log`).
    """
    users = _read()
    public = config.is_public()
    changed = rotated = False
    generated: List[Tuple[str, str]] = []
    for d in DEFAULT_USERS:
        name = d["username"]
        u = next((x for x in users if x["username"] == name), None)
        pw = _env_password(name)
        if pw and len(pw) < MIN_PASSWORD_LEN:
            log(f"PERINGATAN: SEMPAT_{name.upper()}_PASSWORD kurang dari {MIN_PASSWORD_LEN} karakter, diabaikan.")
            pw = ""
        if u is None:
            u = {"username": name, "role": d["role"], "name": d["name"]}
            if pw or public:
                u["name"] = d["name"].replace(" (demo)", "")
            users.append(u)
        elif not (pw or public):
            continue  # server lokal, akun sudah ada: biarkan (termasuk kata sandi yang diganti manual)
        if pw:
            if "hash" not in u or not _matches(u, pw):
                if public and "hash" in u and _matches(u, d["password"]):
                    rotated = True
                _set_pw(u, pw)
                changed = True
        elif public:
            if "hash" not in u or _matches(u, d["password"]):
                rotated = rotated or "hash" in u
                new = secrets.token_urlsafe(12)
                _set_pw(u, new)
                u["name"] = d["name"].replace(" (demo)", "")
                generated.append((name, new))
                changed = True
        else:
            _set_pw(u, d["password"])  # server lokal, akun baru: akun demo
            changed = True
    if changed:
        _write(users)
    if rotated and config.SECRET_PATH.exists():
        config.SECRET_PATH.unlink()  # token lama (masuk dengan kata sandi demo) tidak berlaku lagi
    for name, new in generated:
        log(f"  Akun {name}: kata sandi acak = {new}   (dicetak sekali; atur SEMPAT_{name.upper()}_PASSWORD "
            f"agar tetap, atau ganti: python -m backend.auth set {name} <kata_sandi>)")
    return generated


def demo_accounts_allowed() -> bool:
    """Akun demo hanya boleh dipakai di server lokal/LAN."""
    return not config.is_public()


def _load() -> list:
    if not config.USERS_PATH.exists():
        ensure_accounts()
    return _read()


def _secret() -> bytes:
    if not config.SECRET_PATH.exists():
        config.SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SECRET_PATH.write_text(secrets.token_hex(32), encoding="utf-8")
    return bytes.fromhex(config.SECRET_PATH.read_text(encoding="utf-8").strip())


def verify(username: str, password: str) -> Optional[dict]:
    demo = _DEFAULT_PW.get(username)
    if demo is not None and not demo_accounts_allowed() and hmac.compare_digest(demo.encode(), password.encode()):
        _hash(password, b"timing-equaliser")  # samakan waktu respons
        return None  # kata sandi demo tidak pernah diterima di server publik
    for u in _load():
        if u["username"] == username:
            if _matches(u, password):
                return {"username": u["username"], "role": u["role"], "name": u.get("name", u["username"])}
            return None
    _hash(password, b"timing-equaliser")  # samakan waktu respons
    return None


def issue(user: dict) -> str:
    body = {"u": user["username"], "r": user["role"], "n": user["name"], "exp": int(time.time()) + config.TOKEN_TTL_S}
    raw = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(_secret(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def check(token: str) -> Optional[dict]:
    try:
        raw, sig = token.split(".", 1)
    except ValueError:
        return None
    good = hmac.new(_secret(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(good, sig):
        return None
    try:
        body = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except (ValueError, json.JSONDecodeError):
        return None
    if body.get("exp", 0) < time.time():
        return None
    return {"username": body["u"], "role": body["r"], "name": body.get("n", body["u"])}


def set_password(username: str, password: str, role: Optional[str] = None) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Kata sandi minimal {MIN_PASSWORD_LEN} karakter.")
    users = _load()
    for u in users:
        if u["username"] == username:
            _set_pw(u, password)
            if role:
                u["role"] = role
            break
    else:
        u = {"username": username, "role": role or "petugas", "name": username}
        _set_pw(u, password)
        users.append(u)
    _write(users)


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "set":
        try:
            set_password(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
        except ValueError as exc:
            sys.exit(str(exc))
        print(f"Kata sandi '{sys.argv[2]}' diperbarui.")
    else:
        print("Pemakaian: python -m backend.auth set <username> <kata_sandi> [role]")
        sys.exit(1)
