"""Akun petugas/perencana dan token bertanda tangan (tanpa dependensi luar).

Ganti kata sandi bawaan:  python -m backend.auth set <username> <kata_sandi_baru>
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
from typing import Optional

from . import config

ITERATIONS = 200_000
ROLES = ("petugas", "perencana", "admin")

DEFAULT_USERS = [
    {"username": "petugas", "password": "petugas123", "role": "petugas", "name": "Petugas Lapangan (demo)"},
    {"username": "perencana", "password": "perencana123", "role": "perencana", "name": "Perencana BPBD (demo)"},
]


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS).hex()


def _load() -> list:
    if not config.USERS_PATH.exists():
        users = []
        for u in DEFAULT_USERS:
            salt = secrets.token_bytes(16)
            users.append({"username": u["username"], "role": u["role"], "name": u["name"],
                          "salt": salt.hex(), "hash": _hash(u["password"], salt)})
        config.USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.USERS_PATH.write_text(json.dumps(users, indent=2), encoding="utf-8")
    return json.loads(config.USERS_PATH.read_text(encoding="utf-8"))


def _secret() -> bytes:
    if not config.SECRET_PATH.exists():
        config.SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SECRET_PATH.write_text(secrets.token_hex(32), encoding="utf-8")
    return bytes.fromhex(config.SECRET_PATH.read_text(encoding="utf-8").strip())


def verify(username: str, password: str) -> Optional[dict]:
    for u in _load():
        if u["username"] == username:
            if hmac.compare_digest(u["hash"], _hash(password, bytes.fromhex(u["salt"]))):
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
    users = _load()
    salt = secrets.token_bytes(16)
    for u in users:
        if u["username"] == username:
            u["salt"], u["hash"] = salt.hex(), _hash(password, salt)
            if role:
                u["role"] = role
            break
    else:
        users.append({"username": username, "role": role or "petugas", "name": username,
                      "salt": salt.hex(), "hash": _hash(password, salt)})
    config.USERS_PATH.write_text(json.dumps(users, indent=2), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "set":
        set_password(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
        print(f"Kata sandi '{sys.argv[2]}' diperbarui.")
    else:
        print("Pemakaian: python -m backend.auth set <username> <kata_sandi> [role]")
        sys.exit(1)
