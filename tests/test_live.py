"""Uji komponen peta nyata: dekoder PNG, elevasi Terrarium, Overpass, pembangun area live, enkripsi push."""
import io
import json
import math
import os
import struct
import unittest
import zlib

from backend import live_area, osm_fetch, pngdec, synthetic, webpush


def _png(width, height, rows, ch=3, filters=None):
    """Encoder PNG kecil dengan filter per baris yang bisa dipilih (untuk menguji semua filter)."""
    def chunk(t, b):
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xFFFFFFFF)
    raw = bytearray()
    prev = bytes(width * ch)
    for y, row in enumerate(rows):
        f = (filters or [0])[y % len(filters or [0])]
        line = bytearray(row)
        enc = bytearray(len(line))
        for x in range(len(line)):
            a = line[x - ch] if x >= ch else 0
            b = prev[x]
            c = prev[x - ch] if x >= ch else 0
            pred = [0, a, b, (a + b) >> 1, pngdec._paeth(a, b, c)][f]
            enc[x] = (line[x] - pred) & 0xFF
        raw += bytes([f]) + enc
        prev = bytes(line)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2 if ch == 3 else 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b"")


def _terrarium_tile(fn, z, tx, ty):
    rows = []
    for py in range(256):
        row = bytearray()
        for px in range(256):
            # koordinat pusat piksel -> lat/lon
            n = 2 ** z
            lon = (tx + (px + 0.5) / 256) / n * 360 - 180
            yy = (ty + (py + 0.5) / 256) / n
            lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy))))
            v = fn(lat, lon) + 32768
            r = int(v // 256)
            g = int(v % 256)
            b = int(round((v - math.floor(v)) * 256)) % 256
            row += bytes([r, g, b])
        rows.append(bytes(row))
    return _png(256, 256, rows, filters=[0, 1, 2, 3, 4])


class TestPNG(unittest.TestCase):
    def test_all_filters(self):
        import random
        rnd = random.Random(3)
        for ch in (3, 4):
            rows = [bytes(rnd.randrange(256) for _ in range(17 * ch)) for _ in range(11)]
            data = _png(17, 11, rows, ch=ch, filters=[0, 1, 2, 3, 4])
            w, h, c, pix = pngdec.decode(data)
            self.assertEqual((w, h, c), (17, 11, ch))
            self.assertEqual(bytes(pix), b"".join(rows))


class TestTerrarium(unittest.TestCase):
    def test_bilinear_matches_surface(self):
        def surface(lat, lon):  # bidang miring: 0 m di pantai, naik ke utara
            return max(0.0, (lat + 8.25) * 100000 / 100 * 1.0)
        fetched = []

        def fetch(url):
            z, x, y = url.split("/")[-3:]
            fetched.append(url)
            return _terrarium_tile(surface, int(z), int(x), int(y.split(".")[0]))
        dem = osm_fetch.TerrariumDEM(cache_dir=None, z=13, fetch=fetch)
        pts = [(-8.24, 111.10), (-8.23, 111.105), (-8.22, 111.11)]
        vals = dem(pts)
        for (la, lo), v in zip(pts, vals):
            self.assertAlmostEqual(v, surface(la, lo), delta=1.0)
        self.assertLessEqual(len(set(fetched)), 4)


class TestOverpass(unittest.TestCase):
    def test_query_and_facilities(self):
        q = osm_fetch.overpass_query((-8.3, 111.0, -8.2, 111.1), buildings=True)
        self.assertIn('way["highway"]', q)
        self.assertIn('way["building"]', q)
        self.assertIn('"natural"="coastline"', q)
        self.assertIn("out center", q)
        fac = osm_fetch.extract_facilities({"elements": [
            {"type": "node", "id": 1, "lat": -8.2, "lon": 111.0, "tags": {"amenity": "hospital", "name": "RSUD"}},
            {"type": "way", "id": 2, "center": {"lat": -8.21, "lon": 111.01}, "tags": {"amenity": "police", "name": "Polsek"}},
            {"type": "node", "id": 3, "lat": -8.22, "lon": 111.02, "tags": {"emergency": "assembly_point", "assembly_point:tsunami": "yes"}},
            {"type": "node", "id": 4, "lat": -8.22, "lon": 111.02, "tags": {"amenity": "clinic"}},  # tanpa nama -> dilewati
        ]})
        self.assertEqual([f["kind"] for f in fac], ["faskes", "polisi", "titik_kumpul_osm"])
        self.assertTrue(fac[2]["tsunami"])


class TestLiveBuilder(unittest.TestCase):
    def setUp(self):
        os.environ["SEMPAT_FAKE_OSM"] = "1"

    def tearDown(self):
        os.environ.pop("SEMPAT_FAKE_OSM", None)

    def test_ids_and_cover(self):
        self.assertEqual(live_area.live_id(-0.9471, 100.3540), "live-s00095-e10035")
        self.assertEqual(live_area.live_id(5.554, -95.31), "live-n00555-w09531")
        meta = {"bbox": [100.33, -0.97, 100.37, -0.93]}
        self.assertTrue(live_area.covers(meta, -0.95, 100.35))
        self.assertFalse(live_area.covers(meta, -0.931, 100.35))  # terlalu dekat tepi

    def test_expands_inland_when_no_safe_place(self):
        calls = []
        base = live_area._test_source()

        def src(bbox, buildings):
            calls.append(bbox)
            data, _ = base(bbox, buildings)

            def flat(points):  # dataran rendah: tidak ada node aman di kotak kecil
                return [2.0] * len(points)
            flat.name = "datar"
            return data, flat
        area = live_area.build_around(-8.20, 111.10, source=src, log=lambda *_: None)
        self.assertEqual(len(calls), 2)
        self.assertTrue(area["meta"].get("expanded"))
        s0, w0, n0, e0 = calls[0]
        s1, w1, n1, e1 = calls[1]
        self.assertGreater((n1 - s1), (n0 - s0))  # kotak kedua lebih besar
        self.assertTrue(live_area.covers(area["meta"], -8.20, 111.10, margin_m=500))


class TestWebPushCrypto(unittest.TestCase):
    def test_round_trip_decrypt(self):
        """Dekripsi sisi HP (RFC 8291) — memastikan pesan bisa dibaca browser."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        ua = ec.generate_private_key(ec.SECP256R1())
        ua_pub = ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        auth = os.urandom(16)
        msg = json.dumps({"title": "PERINGATAN DINI TSUNAMI — SIMULASI"}).encode()
        body = webpush.encrypt(msg, webpush.b64u(ua_pub), webpush.b64u(auth))
        salt, rs, idlen = body[:16], struct.unpack(">I", body[16:20])[0], body[20]
        as_pub = body[21:21 + idlen]
        ct = body[21 + idlen:]
        self.assertEqual(rs, 4096)
        secret = ua.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
        h = webpush._hmac
        ikm = h(h(auth, secret), b"WebPush: info\x00" + ua_pub + as_pub + b"\x01")
        prk = h(salt, ikm)
        cek = h(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
        nonce = h(prk, b"Content-Encoding: nonce\x00\x01")[:12]
        pt = AESGCM(cek).decrypt(nonce, ct, None)
        self.assertEqual(pt.rstrip(b"\x00")[:-1], msg)
        self.assertEqual(pt.rstrip(b"\x00")[-1], 2)


if __name__ == "__main__":
    unittest.main()
