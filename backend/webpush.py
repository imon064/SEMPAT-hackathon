"""Web Push (notifikasi sistem di HP walau aplikasi tertutup) tanpa layanan pihak ketiga berbayar.

Mengikuti standar:
- RFC 8291 + RFC 8188 : enkripsi isi pesan (aes128gcm)
- RFC 8292 (VAPID)     : identitas server lewat JWT ES256
Pesan dikirim ke layanan push milik browser (FCM untuk Chrome/Android, Mozilla, Apple),
yang lalu membangunkan service worker di HP.
Hanya memakai pustaka `cryptography`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAVE_CRYPTO = True
except ImportError:  # pragma: no cover
    HAVE_CRYPTO = False


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64u_dec(s: str) -> bytes:
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def _pub_bytes(pub) -> bytes:
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


# ---------------------------------------------------------------------------
# Enkripsi isi (RFC 8291)
# ---------------------------------------------------------------------------
def encrypt(payload: bytes, ua_public_b64: str, auth_b64: str, *, salt: Optional[bytes] = None,
            as_private=None, rs: int = 4096) -> bytes:
    ua_public = b64u_dec(ua_public_b64)
    auth_secret = b64u_dec(auth_b64)
    if len(payload) > rs - 17 - 86:
        raise ValueError("Isi notifikasi terlalu panjang")
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    as_public = _pub_bytes(as_private.public_key())
    ecdh = as_private.exchange(ec.ECDH(), ua_key)
    # HKDF(auth_secret, ecdh, "WebPush: info" || 0 || ua_public || as_public, 32)
    prk_key = _hmac(auth_secret, ecdh)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    salt = salt or os.urandom(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # 0x02 = rekaman terakhir
    header = salt + struct.pack(">I", rs) + bytes([len(as_public)]) + as_public
    return header + ct


# ---------------------------------------------------------------------------
# VAPID
# ---------------------------------------------------------------------------
class Vapid:
    def __init__(self, private_key):
        self.key = private_key
        self.public_b64 = b64u(_pub_bytes(private_key.public_key()))

    @classmethod
    def load_or_create(cls, path: Path, env_value: str = "") -> "Vapid":
        if env_value:
            text = env_value.strip()
            if "BEGIN" in text:
                key = serialization.load_pem_private_key(text.replace("\\n", "\n").encode(), None)
            else:
                key = ec.derive_private_key(int.from_bytes(b64u_dec(text), "big"), ec.SECP256R1())
            return cls(key)
        if path.exists():
            return cls(serialization.load_pem_private_key(path.read_bytes(), None))
        key = ec.generate_private_key(ec.SECP256R1())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
        return cls(key)

    def raw_private_b64(self) -> str:
        return b64u(self.key.private_numbers().private_value.to_bytes(32, "big"))

    def jwt(self, endpoint: str, sub: str, ttl_s: int = 12 * 3600) -> str:
        u = urllib.parse.urlsplit(endpoint)
        header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
        claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + ttl_s,
                                  "sub": sub}, separators=(",", ":")).encode())
        signing = f"{header}.{claims}".encode()
        r, s = decode_dss_signature(self.key.sign(signing, ec.ECDSA(hashes.SHA256())))
        return f"{header}.{claims}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def send(sub: dict, payload: dict, vapid: Vapid, *, contact: str, ttl: int = 3600, urgency: str = "high",
         timeout: float = 15) -> Tuple[int, str]:
    """Kirim satu pesan. Mengembalikan (kode HTTP, teks). 404/410 = langganan sudah tidak berlaku."""
    endpoint = sub["endpoint"]
    keys = sub.get("keys") or {}
    body = encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8"), keys["p256dh"], keys["auth"])
    headers = {
        "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
        "TTL": str(ttl), "Urgency": urgency,
        "Authorization": f"vapid t={vapid.jwt(endpoint, contact)}, k={vapid.public_b64}",
    }
    req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]


if __name__ == "__main__":  # python -m backend.webpush  -> buat kunci VAPID untuk server cloud
    v = Vapid(ec.generate_private_key(ec.SECP256R1()))
    print("Isi variabel lingkungan berikut di server cloud (Render dsb.) agar langganan notifikasi tidak hilang:")
    print(f"SEMPAT_VAPID_PRIVATE={v.raw_private_b64()}")
    print(f"(kunci publik: {v.public_b64})")
