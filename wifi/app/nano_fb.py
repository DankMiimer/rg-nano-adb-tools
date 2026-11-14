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
import select
import struct
from typing import Iterator

FB_W = 240
FB_H = 240
BPP = 2
STRIDE = FB_W * BPP         # 480
FRAME = STRIDE * FB_H       # 115200 bytes for one visible frame

FBDEV = os.environ.get("NANO_FB", "/dev/fb0")
EVDEV = os.environ.get("NANO_EVDEV", "/dev/input/event0")
TTYDEV = os.environ.get("NANO_TTY", "/dev/tty0")
FKGPIOD = os.environ.get("NANO_FKGPIOD", "/etc/fkgpiod.conf")


def is_gba_mini() -> bool:
    """DrUm78's GBA Mini OS maps Start+A/B to X/Y (no dedicated X/Y buttons)."""
    try:
        with open(FKGPIOD) as f:
            return "START+A" in f.read().replace(" ", "")
    except OSError:
        return False


def detect_screen() -> tuple[int, int, int, int]:
    """Visible window (x, y, w, h) inside the 240x240 framebuffer.
    GBA Mini's 240x160 panel shows rows 48..207; RG Nano and FunKey S show
    all 240x240."""
    want = os.environ.get("NANO_SCREEN", "")
    if want:
        try:
            size, x, y = want.split("+")
            w, h = size.split("x")
            return int(x), int(y), int(w), int(h)
        except ValueError:
            pass
    if is_gba_mini():
        return 0, 48, 240, 160
    return 0, 0, FB_W, FB_H


WX, WY, W, H = detect_screen()

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


# Dark midnight-slate + cyan palette (high contrast on small LCDs).
class Pal:
    BG = rgb565(13, 18, 28)       # deep midnight slate
    BG2 = rgb565(20, 28, 42)      # header / elevated surface
    CARD = rgb565(19, 26, 38)     # card container fill
    BORDER = rgb565(46, 62, 88)   # crisp card / rule border
    INK = rgb565(214, 224, 240)   # primary body text
    DIM = rgb565(120, 138, 166)   # muted / secondary text
    HI = rgb565(248, 250, 255)    # crisp white highlight
    SEL = rgb565(34, 54, 84)      # selected row fill
    ACCENT = rgb565(56, 189, 248) # electric cyan accent
    OK = rgb565(52, 211, 153)     # emerald ready / synced
    OK_BG = rgb565(16, 62, 50)
    WARN = rgb565(251, 191, 36)   # amber pending / attention
    WARN_BG = rgb565(68, 48, 16)
    ERR = rgb565(248, 113, 113)   # coral offline / error
    ERR_BG = rgb565(74, 26, 32)
    BLACK = rgb565(0, 0, 0)
    WHITE = rgb565(255, 255, 255)


class Framebuffer:
    def __init__(self, screen: tuple[int, int, int, int] | None = None) -> None:
        if screen is None:
            screen = detect_screen()
        self.wx, self.wy, self.w, self.h = screen
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
        if self.wx == 0 and self.wy == 0 and self.w == FB_W and self.h == FB_H:
            self.buf[:] = bytes((lo, hi)) * (FB_W * FB_H)
        else:
            self.buf[:] = b"\x00" * FRAME
            self.fill_rect(0, 0, self.w, self.h, color)

    def fill_rect(self, x: int, y: int, w: int, h: int, color: int) -> None:
        x0 = max(0, x); y0 = max(0, y)
        x1 = min(self.w, x + w); y1 = min(self.h, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        lo = color & 0xFF
        hi = (color >> 8) & 0xFF
        row = bytes((lo, hi)) * (x1 - x0)
        for yy in range(y0, y1):
            off = (self.wy + yy) * STRIDE + (self.wx + x0) * BPP
            self.buf[off:off + len(row)] = row

    def rect(self, x: int, y: int, w: int, h: int, color: int, t: int = 1) -> None:
        self.fill_rect(x, y, w, t, color)
        self.fill_rect(x, y + h - t, w, t, color)
        self.fill_rect(x, y, t, h, color)
        self.fill_rect(x + w - t, y, t, h, color)

    def px(self, x: int, y: int, color: int) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            off = (self.wy + y) * STRIDE + (self.wx + x) * BPP
            self.buf[off] = color & 0xFF
            self.buf[off + 1] = (color >> 8) & 0xFF

    def fill_pill(self, x: int, y: int, w: int, h: int, fill: int, border: int | None = None) -> None:
        """Draw a chamfered pill/card rectangle (1px clipped corners when w,h >= 5)."""
        if w <= 0 or h <= 0:
            return
        if w < 5 or h < 5:
            self.fill_rect(x, y, w, h, fill)
            return
        self.fill_rect(x + 1, y, w - 2, 1, border if border is not None else fill)
        self.fill_rect(x + 1, y + h - 1, w - 2, 1, border if border is not None else fill)
        if border is not None:
            self.fill_rect(x + 1, y + 1, w - 2, h - 2, fill)
            self.fill_rect(x, y + 1, 1, h - 2, border)
            self.fill_rect(x + w - 1, y + 1, 1, h - 2, border)
        else:
            self.fill_rect(x, y + 1, w, h - 2, fill)

    def bitmap(self, x: int, y: int, rows: tuple[int, ...] | list[int], color: int, width: int = 8) -> None:
        """Draw a crisp 1-bit monochrome pixel icon (MSB on the left)."""
        for ry, bits in enumerate(rows):
            for rx in range(width):
                if bits & (1 << (width - 1 - rx)):
                    self.px(x + rx, y + ry, color)

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
        self.text((self.w - self.text_width(s, scale)) // 2, y, s, color, scale, bg)

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

    def poll(self, repeat: bool = False) -> str | None:
        """Non-blocking button check; drains pending events and returns first press."""
        want = (1, 2) if repeat else (1,)
        while True:
            try:
                r, _, _ = select.select([self.fd], [], [], 0)
            except (OSError, ValueError):
                return None
            if not r:
                return None
            try:
                data = os.read(self.fd, EV_SIZE)
            except OSError:
                return None
            if len(data) < EV_SIZE:
                return None
            _, _, etype, code, value = struct.unpack(EV_FMT, data)
            if etype == EV_KEY and value in want and code in BTN:
                return BTN[code]

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

    def poll(self, repeat: bool = False) -> str | None:
        """Non-blocking button check; drains pending events and returns first press."""
        want = (1, 2) if repeat else (1,)
        while True:
            try:
                r, _, _ = select.select([self.fd], [], [], 0)
            except (OSError, ValueError):
                return None
            if not r:
                return None
            try:
                data = os.read(self.fd, EV_SIZE)
            except OSError:
                return None
            if len(data) < EV_SIZE:
                return None
            _, _, etype, code, value = struct.unpack(EV_FMT, data)
            if etype == EV_KEY and value in want and code in BTN:
                return BTN[code]

    def close(self) -> None:
        os.close(self.fd)
