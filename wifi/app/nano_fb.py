#!/usr/bin/env python3
"""
nano_fb -- tiny pure-Python framebuffer + input toolkit for the RG Nano.

No SDL, no pygame, no PIL on the device -- we mmap /dev/fb0 (240x240 RGB565,
triple-buffered = 240x720 virtual) and draw RGB565 pixels into a backbuffer,
then copy it into all three buffer slots so whichever the panel is showing
displays our frame. Input is read straight from the evdev keyboard at
/dev/input/event0 using the FunKey button->keycode map (D-pad sends letters,
not arrows). The console VT is put in KD_GRAPHICS so the text console does not
draw over our pixels.

This is the graphical replacement for the old st/terminal UI.
"""

from __future__ import annotations

import fcntl
import mmap
import os
import struct
from typing import Iterator

W = 240
H = 240
BPP = 2
STRIDE = W * BPP            # 480
FRAME = STRIDE * H          # 115200 bytes for one visible frame

FBDEV = os.environ.get("NANO_FB", "/dev/fb0")
EVDEV = os.environ.get("NANO_EVDEV", "/dev/input/event0")
TTYDEV = os.environ.get("NANO_TTY", "/dev/tty0")

# ioctls
KDSETMODE = 0x4B3A
KD_TEXT = 0x00
KD_GRAPHICS = 0x01
FBIOGET_VSCREENINFO = 0x4600
FBIOPAN_DISPLAY = 0x4606

# evdev input_event on 32-bit ARM: struct timeval(8) + type(2) + code(2) + value(4)
EV_FMT = "llHHi"
EV_SIZE = struct.calcsize(EV_FMT)   # 16 on 32-bit
EV_KEY = 0x01

# FunKey button -> evdev keycode (from fkgpiod.conf). D-pad = letters.
BTN = {
    22: "up",     # KEY_U
    32: "down",   # KEY_D
    38: "left",   # KEY_L
    19: "right",  # KEY_R
    30: "a",      # KEY_A  -> confirm/select
    48: "b",      # KEY_B  -> back/cancel
    45: "x",      # KEY_X
    21: "y",      # KEY_Y
    31: "start",  # KEY_S
    16: "menu",   # KEY_Q
    37: "fn",     # KEY_K
    50: "l",      # KEY_M  (L shoulder)
    49: "r",      # KEY_N  (R shoulder)
}


def rgb565(r: int, g: int, b: int) -> int:
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


# A small retro palette (Game Boy / MinUI flavored). Override per-theme.
class Pal:
    BG = rgb565(15, 56, 15)       # deep GB green
    BG2 = rgb565(24, 72, 24)      # panel
    INK = rgb565(155, 188, 15)    # bright GB lime (text)
    DIM = rgb565(96, 130, 40)     # muted text
    HI = rgb565(220, 240, 170)    # highlight text
    SEL = rgb565(48, 98, 48)      # selection bar
    ACCENT = rgb565(232, 80, 60)  # red accent
    BLACK = rgb565(0, 0, 0)
    WHITE = rgb565(255, 255, 255)


class Framebuffer:
    def __init__(self) -> None:
        # Headless mode (offline preview on a dev box with no /dev/fb0).
        if os.environ.get("NANO_FB_HEADLESS"):
            self.fd = None
            self.mm = None
            self.slots = 1
            self.map_len = FRAME
            self.buf = bytearray(FRAME)
            self._tty = None
            return
        self.fd = os.open(FBDEV, os.O_RDWR)
        # virtual yres tells us how many buffer slots exist (240, 480 or 720).
        # The fbtft ST7789 panel is triple-buffered (240x720 virtual) but the
        # SPI driver flushes the visible region via deferred-IO, and the real
        # video memory is < 3 full frames -- writing all 3 buffers SIGBUSes.
        # We map and draw only buffer 0 and pan the display there.
        self.slots = 1
        self.map_len = FRAME
        self.mm = mmap.mmap(self.fd, self.map_len, mmap.MAP_SHARED,
                            mmap.PROT_READ | mmap.PROT_WRITE)
        # Best-effort: force the panel to show buffer 0 (xoffset/yoffset = 0).
        try:
            vinfo = bytearray(160)
            fcntl.ioctl(self.fd, FBIOGET_VSCREENINFO, vinfo, True)
            struct.pack_into("<II", vinfo, 16, 0, 0)   # xoffset@16, yoffset@20
            fcntl.ioctl(self.fd, FBIOPAN_DISPLAY, vinfo, True)
        except Exception:
            pass
        # 16-bit backbuffer as bytes; we draw here then flush.
        self.buf = bytearray(FRAME)
        self._tty = None

    # --- drawing -------------------------------------------------------- #
    def fill(self, color: int) -> None:
        lo = color & 0xFF
        hi = (color >> 8) & 0xFF
        self.buf[:] = bytes((lo, hi)) * (W * H)

    def fill_rect(self, x: int, y: int, w: int, h: int, color: int) -> None:
        x0 = max(0, x); y0 = max(0, y)
        x1 = min(W, x + w); y1 = min(H, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        lo = color & 0xFF
        hi = (color >> 8) & 0xFF
        row = bytes((lo, hi)) * (x1 - x0)
        for yy in range(y0, y1):
            off = yy * STRIDE + x0 * BPP
            self.buf[off:off + len(row)] = row

    def rect(self, x: int, y: int, w: int, h: int, color: int, t: int = 1) -> None:
        self.fill_rect(x, y, w, t, color)
        self.fill_rect(x, y + h - t, w, t, color)
        self.fill_rect(x, y, t, h, color)
        self.fill_rect(x + w - t, y, t, h, color)

    def px(self, x: int, y: int, color: int) -> None:
        if 0 <= x < W and 0 <= y < H:
            off = y * STRIDE + x * BPP
            self.buf[off] = color & 0xFF
            self.buf[off + 1] = (color >> 8) & 0xFF

    def text(self, x: int, y: int, s: str, color: int, scale: int = 1,
             bg: int | None = None, fnt=None) -> int:
        """Draw a proportional string. fnt=(FONT,GLYPH_H) overrides the default
        (e.g. a smaller font for the dense keyboard). Returns end x."""
        if fnt is None:
            from nano_font import FONT, GLYPH_H
        else:
            FONT, GLYPH_H = fnt
        fallback = FONT.get("?", (6, [0] * GLYPH_H))
        chh = GLYPH_H * scale
        cx = x
        for c in s:
            w, rows = FONT.get(c, fallback)
            if bg is not None:
                self.fill_rect(cx, y, w * scale, chh, bg)
            for row in range(GLYPH_H):
                bits = rows[row]
                col = 0
                while bits:
                    if bits & 1:
                        if scale == 1:
                            self.px(cx + col, y + row, color)
                        else:
                            self.fill_rect(cx + col * scale, y + row * scale,
                                           scale, scale, color)
                    bits >>= 1
                    col += 1
            cx += w * scale
        return cx

    def text_width(self, s: str, scale: int = 1, fnt=None) -> int:
        if fnt is None:
            from nano_font import FONT
        else:
            FONT = fnt[0]
        fb = FONT.get("?", (6, []))
        return sum(FONT.get(c, fb)[0] for c in s) * scale

    def text_center(self, y: int, s: str, color: int, scale: int = 1,
                    bg: int | None = None) -> None:
        self.text((W - self.text_width(s, scale)) // 2, y, s, color, scale, bg)

    def flush(self) -> None:
        if self.mm is None:
            return
        frame = bytes(self.buf)
        for s in range(self.slots):
            self.mm[s * FRAME:(s + 1) * FRAME] = frame

    # --- VT / lifecycle ------------------------------------------------- #
    def enter_graphics(self) -> None:
        # NANO_NO_VT=1 leaves the VT mode untouched (e.g. when co-existing with a
        # suspended gmenu2x that already put the console in KD_GRAPHICS).
        if os.environ.get("NANO_NO_VT"):
            return
        try:
            self._tty = os.open(TTYDEV, os.O_RDWR)
            fcntl.ioctl(self._tty, KDSETMODE, KD_GRAPHICS)
        except Exception:
            self._tty = None

    def leave_graphics(self) -> None:
        if self._tty is not None:
            try:
                fcntl.ioctl(self._tty, KDSETMODE, KD_TEXT)
                os.close(self._tty)
            except Exception:
                pass
            self._tty = None

    def close(self) -> None:
        if self.mm is None:
            return
        try:
            self.mm.close()
        finally:
            os.close(self.fd)


class Buttons:
    """Blocking reader for the evdev keyboard; yields logical button names."""

    def __init__(self) -> None:
        self.fd = os.open(EVDEV, os.O_RDONLY)

    def get(self, repeat: bool = True) -> str:
        want = (1, 2) if repeat else (1,)
        while True:
            data = os.read(self.fd, EV_SIZE)
            if len(data) < EV_SIZE:
                continue
            _, _, etype, code, value = struct.unpack(EV_FMT, data)
            if etype == EV_KEY and value in want and code in BTN:
                return BTN[code]

    def close(self) -> None:
        os.close(self.fd)
