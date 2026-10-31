"""Dekoder PNG minimal (stdlib saja) untuk ubin elevasi Terrarium.

Mendukung PNG 8-bit, tanpa interlace, tipe warna RGB (2) dan RGBA (6) —
format yang dipakai ubin elevasi AWS Terrain Tiles.
"""
from __future__ import annotations

import struct
import zlib
from typing import Tuple

SIG = b"\x89PNG\r\n\x1a\n"


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def decode(data: bytes) -> Tuple[int, int, int, bytearray]:
    """Mengembalikan (lebar, tinggi, kanal, piksel) dengan piksel baris demi baris."""
    if data[:8] != SIG:
        raise ValueError("Bukan berkas PNG")
    pos = 8
    width = height = depth = ctype = interlace = None
    idat = bytearray()
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype_chunk = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype_chunk == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif ctype_chunk == b"IDAT":
            idat += body
        elif ctype_chunk == b"IEND":
            break
    if depth != 8 or interlace != 0 or ctype not in (2, 6):
        raise ValueError(f"Format PNG tidak didukung (depth={depth}, color={ctype}, interlace={interlace})")
    ch = 3 if ctype == 2 else 4
    raw = zlib.decompress(bytes(idat))
    stride = width * ch
    out = bytearray(height * stride)
    prev = bytearray(stride)
    i = 0
    for y in range(height):
        f = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        if f == 1:
            for x in range(ch, stride):
                line[x] = (line[x] + line[x - ch]) & 0xFF
        elif f == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif f == 3:
            for x in range(stride):
                left = line[x - ch] if x >= ch else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 0xFF
        elif f == 4:
            for x in range(stride):
                left = line[x - ch] if x >= ch else 0
                ul = prev[x - ch] if x >= ch else 0
                line[x] = (line[x] + _paeth(left, prev[x], ul)) & 0xFF
        elif f != 0:
            raise ValueError(f"Filter PNG tidak dikenal: {f}")
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return width, height, ch, out
