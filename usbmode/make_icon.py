#!/usr/bin/env python3
"""Draw the 32x32 pixel-art icons for the USB Mode OPKs (no PIL needed).

    make_icon.py usb out.png            USB trident  (entry "Switch to USB")
    make_icon.py adb out.png            ">_ ADB"     (entry "Switch to ADB")
    make_icon.py <usb|adb> out.png --preview   also write out_x8.png (8x nearest)
"""
import struct
import sys
import zlib

W = H = 32
CLEAR = (0, 0, 0, 0)
BG = (48, 98, 48, 255)      # GB dark green, same family as the WiFi Setup icon
RIM = (155, 188, 15, 255)   # GB lime
INK = (202, 220, 159, 255)  # pale lime for the glyph

# 3x5 pixel font for the ADB label.
GLYPHS = {
    "A": ("010", "101", "111", "101", "101"),
    "D": ("110", "101", "101", "101", "110"),
    "B": ("110", "101", "110", "101", "110"),
}


class Canvas:
    def __init__(self):
        self.px = [[CLEAR] * W for _ in range(H)]

    def put(self, x, y, c):
        if 0 <= x < W and 0 <= y < H:
            self.px[y][x] = c

    def rect(self, x0, y0, x1, y1, c):  # inclusive corners
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                self.put(x, y, c)

    def disc(self, cx, cy, r, c):
        for y in range(H):
            for x in range(W):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    self.put(x, y, c)

    def tile(self):
        """Rounded tile: lime rim, green fill, transparent corners."""
        for y in range(H):
            for x in range(W):
                corner = min(x, W - 1 - x) + min(y, H - 1 - y)
                if corner < 2:
                    continue
                edge = x in (0, W - 1) or y in (0, H - 1) or corner < 3
                self.px[y][x] = RIM if edge else BG

    def text(self, s, x0, y0, scale, c):
        for k, ch in enumerate(s):
            lx = x0 + k * (3 * scale + scale + 1)
            for r, row in enumerate(GLYPHS[ch]):
                for col, bit in enumerate(row):
                    if bit == "1":
                        self.rect(lx + col * scale, y0 + r * scale,
                                  lx + col * scale + scale - 1, y0 + r * scale + scale - 1, c)


def draw_usb(c):
    """USB trident. Stem is 2px wide at x=15..16."""
    c.rect(15, 7, 16, 23, INK)                    # stem
    c.disc(15.5, 26, 3.0, INK)                    # bottom ring
    for i, (a, b) in enumerate([(15, 16), (14, 17), (13, 18), (12, 19)]):
        c.rect(a, 3 + i, b, 3 + i, INK)           # arrowhead (apex up)
    c.rect(10, 18, 15, 19, INK)                   # left arm out of the stem
    c.rect(9, 13, 10, 19, INK)                    # left riser
    c.disc(9.5, 11.5, 2.6, INK)                   # left end: ring
    c.rect(16, 13, 22, 14, INK)                   # right arm out of the stem
    c.rect(21, 10, 22, 14, INK)                   # right riser
    c.rect(20, 6, 24, 10, INK)                    # right end: square


def draw_adb(c):
    """A shell prompt ">_" over the word ADB (adb is, in practice, a root shell)."""
    for i in range(5):                            # ">" going down to its tip, 3px strokes
        c.rect(7 + i, 4 + i, 9 + i, 4 + i, INK)
    for i in range(4):                            # ">" coming back
        c.rect(10 - i, 9 + i, 12 - i, 9 + i, INK)
    c.rect(17, 11, 25, 12, INK)                   # "_"
    c.text("ADB", 5, 17, 2, INK)                  # 22px wide, centred


ICONS = {"usb": draw_usb, "adb": draw_adb}


def png_bytes(pixels, scale=1):
    rows = []
    for row in pixels:
        line = bytearray()
        for px in row:
            line += bytes(px) * scale
        rows.extend([b"\x00" + bytes(line)] * scale)

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    w, h = len(pixels[0]) * scale, len(pixels) * scale
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
        + chunk(b"IEND", b"")
    )


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ICONS:
        sys.exit(__doc__)
    canvas = Canvas()
    canvas.tile()
    ICONS[sys.argv[1]](canvas)
    out = sys.argv[2]
    with open(out, "wb") as f:
        f.write(png_bytes(canvas.px))
    if "--preview" in sys.argv[3:]:
        with open(out.rsplit(".", 1)[0] + "_x8.png", "wb") as f:
            f.write(png_bytes(canvas.px, 8))
    print("wrote", out)
