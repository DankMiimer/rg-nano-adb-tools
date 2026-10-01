#!/usr/bin/env python3
"""Decode a PNG (8-bit RGB/RGBA, any filter) and print: width height R G B of the
centre pixel. Standard library only; used by the tests to check what the Nano drew."""
import struct
import sys
import zlib

data = open(sys.argv[1], "rb").read()
assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"

pos, idat = 8, b""
width = height = color_type = 0
while pos < len(data):
    length, = struct.unpack(">I", data[pos:pos + 4])
    tag, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
    pos += 12 + length
    if tag == b"IHDR":
        width, height, _depth, color_type = struct.unpack(">IIBB", body[:10])
    elif tag == b"IDAT":
        idat += body

raw = zlib.decompress(idat)
bpp = {2: 3, 6: 4}[color_type]
stride = width * bpp
prev = bytearray(stride)
rows = []
i = 0
for _ in range(height):
    ftype = raw[i]
    line = bytearray(raw[i + 1:i + 1 + stride])
    i += 1 + stride
    for x in range(stride):
        a = line[x - bpp] if x >= bpp else 0
        b = prev[x]
        c = prev[x - bpp] if x >= bpp else 0
        if ftype == 1:
            line[x] = (line[x] + a) & 255
        elif ftype == 2:
            line[x] = (line[x] + b) & 255
        elif ftype == 3:
            line[x] = (line[x] + ((a + b) >> 1)) & 255
        elif ftype == 4:
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            line[x] = (line[x] + (a if pa <= pb and pa <= pc else (b if pb <= pc else c))) & 255
    rows.append(line)
    prev = line

cx, cy = width // 2, height // 2
r, g, b = rows[cy][cx * bpp:cx * bpp + 3]
print(width, height, r, g, b)
