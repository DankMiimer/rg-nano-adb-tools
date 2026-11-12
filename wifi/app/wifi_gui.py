#!/usr/bin/env python3
"""
RG Nano WiFi Setup -- graphical pixel UI.

Pure-framebuffer (nano_fb) app: Game Boy / MinUI palette, RasterForge pixel font,
D-pad navigation, pop-up on-screen keyboard scaled to 240x240. Runs on the
console owning the screen (launched as an OPK, not in st).

All network/clock logic is in wifi_backend.py (wifi.sh + wpa_supplicant on the
stock firmware), so this file is purely the graphical shell.
"""
import sys
import time

from nano_fb import Framebuffer, Buttons, Pal
from nano_font import GLYPH_H
import wifi_backend as be  # backend: turn_on / scan / status / connect / disconnect / sync_clock

# The font atlas is rendered at a small NATIVE crisp size. Big text is
# integer-scaled (BS) so every pixel is a uniform block -- no uneven strokes
# like a direct large render. The dense keyboard grid + footer use 1x.
BS = 2                  # big scale
BIG = GLYPH_H * BS      # big text pixel height
TITLE_H = BIG + 8
FOOT_H = GLYPH_H + 4    # footer is 1x
ROWH = BIG + 5
LINE = BIG + 2          # vertical step for stacked big-text lines

KB_ROWS = [
    list("qwertyuiop"),
    list("asdfghjkl"),
    list("zxcvbnm"),
    list("0123456789"),
    list(".-_@!#$%&*"),
    ["aA", "SPC", "DEL", "OK"],
]


class Gui:
    def __init__(self, screen=None):
        self.fb = Framebuffer(screen)
        self.btns = Buttons()
        self.fb.enter_graphics()
        self.w = self.fb.w
        self.h = self.fb.h
        compact = self.h < 200
        self.title_h = BIG + (4 if compact else 8)
        self.title_y = 2 if compact else 4
        self.foot_h = GLYPH_H + (3 if compact else 4)
        self.rowh = BIG + (2 if compact else 5)

    def close(self):
        try:
            self.fb.leave_graphics()
            self.fb.close()
            self.btns.close()
        except Exception:
            pass

    # ----- chrome ------------------------------------------------------- #
    def header(self, title, status=""):
        fb = self.fb
        fb.fill(Pal.BG)
        fb.fill_rect(0, 0, self.w, self.title_h, Pal.BG2)
        fb.fill_rect(0, self.title_h - 2, self.w, 2, Pal.INK)
        fb.text(5, self.title_y, title, Pal.HI, BS)
        if status:
            fb.text(self.w - fb.text_width(status) - 5,
                    (self.title_h - GLYPH_H) // 2, status, Pal.DIM, 1)

    def footer(self, hint):
        fb = self.fb
        fb.fill_rect(0, self.h - self.foot_h, self.w, self.foot_h, Pal.BG2)
        fb.fill_rect(0, self.h - self.foot_h, self.w, 2, Pal.INK)
        x = (self.w - fb.text_width(hint)) // 2
        fb.text(x, self.h - self.foot_h + 2, hint, Pal.DIM, 1)

    def flush(self):
        self.fb.flush()

    def _draw_lines(self, lines, y0, color):
        """Draw stacked text, using big (BS) for short lines when it fits the
        screen vertically, otherwise compacting gap or falling back to 1x."""
        avail_h = self.h - self.foot_h - y0 - 2
        scales = [BS if self.fb.text_width(ln, scale=BS) <= self.w - 12 else 1 for ln in lines]
        gap = 6
        need = sum(GLYPH_H * sc + gap for sc in scales) - (gap if lines else 0)
        if need > avail_h:
            gap = 3
            need = sum(GLYPH_H * sc + gap for sc in scales) - (gap if lines else 0)
        if need > avail_h:
            scales = [1] * len(lines)
            need = len(lines) * (GLYPH_H + 3) - (3 if lines else 0)
            gap = 2 if need > avail_h else 3
        y = y0
        for ln, sc in zip(lines, scales):
            self.fb.text(8, y, ln, color, sc)
            y += GLYPH_H * sc + gap
        return y

    def busy(self, title, lines):
        self.header(title)
        self._draw_lines(lines, self.title_h + 6, Pal.INK)
        self.flush()

    def message(self, title, lines, hint="A/B back", wait=True):
        self.header(title)
        self._draw_lines(lines, self.title_h + 6, Pal.HI)
        self.footer(hint)
        self.flush()
        if wait:
            self.wait_any()

    def wait_any(self):
        while True:
            if self.btns.get() in ("a", "b", "menu", "start"):
                return

    def confirm(self, title, lines):
        """A yes/no question: True for A, False for B (or Menu/Start)."""
        self.message(title, lines, hint="A yes  B no", wait=False)
        while True:
            k = self.btns.get(repeat=False)
            if k == "a":
                return True
            if k in ("b", "menu", "start"):
                return False

    def _clip(self, s, max_px):
        """Truncate big-font text to max_px, adding '..' if cut."""
        if self.fb.text_width(s, scale=BS) <= max_px:
            return s
        while s and self.fb.text_width(s + "..", scale=BS) > max_px:
            s = s[:-1]
        return s + ".."

    # ----- menu / list -------------------------------------------------- #
    def menu(self, title, items, status="", show_full=False, sel=0):
        """items: list of (label, detail). Returns index, None (back), or 'QUIT'.
        show_full: render the selected item's full label in the footer (for long
        SSIDs that get truncated in the row). sel: the row the cursor starts on."""
        sel = max(0, min(sel, len(items) - 1))
        off = 0
        avail = max(1, (self.h - self.title_h - self.foot_h) // self.rowh)
        while True:
            if sel < off:
                off = sel
            if sel >= off + avail:
                off = sel - avail + 1
            self.header(title, status)
            fb = self.fb
            y = self.title_h + (2 if self.h < 200 else 4)
            for i in range(off, min(len(items), off + avail)):
                lab, det = items[i]
                if i == sel:
                    fb.fill_rect(2, y - 1, self.w - 4, self.rowh - 2, Pal.SEL)
                ink = Pal.HI if i == sel else Pal.INK
                det_w = fb.text_width(det) if det else 0   # detail at 1x
                if det:
                    fb.text(self.w - det_w - 6, y + 2 + (BIG - GLYPH_H) // 2, det,
                            ink if i == sel else Pal.DIM, 1)
                label_max = self.w - 8 - (det_w + 8 if det else 6)
                fb.text(8, y + 2, self._clip(lab, label_max), ink, BS)
                y += self.rowh
            if show_full and items:
                self.footer(items[sel][0])      # full selected SSID (1x, ~30 chars)
            else:
                self.footer("U/D move  A ok  B back")
            self.flush()
            k = self.btns.get()
            if k == "up":
                sel = (sel - 1) % len(items)
            elif k == "down":
                sel = (sel + 1) % len(items)
            elif k == "a":
                return sel
            elif k == "b":
                return None
            elif k in ("menu", "start"):
                return "QUIT"

    # ----- pop-up keyboard --------------------------------------------- #
    def keyboard(self, title, initial=""):
        text = initial
        r = c = 0
        shift = False
        compact = self.h < 200
        in_sc = 1 if compact else BS
        in_h = GLYPH_H * in_sc + 2
        while True:
            r = max(0, min(r, len(KB_ROWS) - 1))
            c = max(0, min(c, len(KB_ROWS[r]) - 1))
            fb = self.fb
            self.header(title, "ABC" if shift else "abc")
            # input field
            in_y = self.title_h + (2 if compact else 4)
            fb.fill_rect(6, in_y, self.w - 12, in_h, Pal.BLACK)
            shown = text
            while shown and fb.text_width(shown, scale=in_sc) > self.w - 22:
                shown = shown[1:]
            fb.text(9, in_y + 1, shown + "_", Pal.HI, in_sc)
            # key grid (1x so 6 rows fit; keys are single chars, still readable)
            top = in_y + in_h + (4 if compact else 6)
            gh = self.h - top - self.foot_h - 2
            cellh = gh // len(KB_ROWS)
            for ri, row in enumerate(KB_ROWS):
                cellw = (self.w - 8) // len(row)
                for ci, key in enumerate(row):
                    kx = 4 + ci * cellw
                    ky = top + ri * cellh
                    seld = (ri == r and ci == c)
                    fb.fill_rect(kx + 1, ky + 1, cellw - 2, cellh - 2,
                                 Pal.ACCENT if seld else Pal.BG2)
                    lab = key.upper() if (shift and len(key) == 1) else key
                    fb.text(kx + (cellw - fb.text_width(lab)) // 2,
                            ky + (cellh - GLYPH_H) // 2, lab,
                            Pal.BLACK if seld else Pal.INK, 1)
            self.footer("A type  B done  X spc  Y del")
            self.flush()
            k = self.btns.get()
            if k == "up":
                r = (r - 1) % len(KB_ROWS)
            elif k == "down":
                r = (r + 1) % len(KB_ROWS)
            elif k == "left":
                c = (c - 1) % len(KB_ROWS[r])
            elif k == "right":
                c = (c + 1) % len(KB_ROWS[r])
            elif k == "x":
                text += " "
            elif k == "y":
                text = text[:-1]
            elif k == "b":
                return text
            elif k in ("menu", "start"):
                return text
            elif k == "a":
                key = KB_ROWS[r][c]
                if key == "OK":
                    return text
                elif key == "DEL":
                    text = text[:-1]
                elif key == "SPC":
                    text += " "
                elif key == "aA":
                    shift = not shift
                elif len(key) == 1:
                    text += key.upper() if shift else key


# --------------------------------------------------------------------------- #
# Screens (wired to wifi_nano backend)
# --------------------------------------------------------------------------- #
def wifi_on(g):
    """Start Wi-Fi if needed; False (after saying why) if it can't."""
    if be.status().get("on"):
        return True
    g.busy("Wi-Fi", ["Starting Wi-Fi..."])
    err = be.turn_on()
    if err:
        g.message("Wi-Fi", err)
        return False
    return True


def screen_scan(g):
    if not wifi_on(g):
        return
    g.busy("Scan", ["Scanning for", "networks..."])
    nets = be.scan()
    if not nets:
        g.message("Scan", ["No networks found.", "Is the dongle in?"])
        return
    items = []
    for n in nets:
        lock = "*" if n["enc"] else " "
        items.append((lock + n["ssid"], be.signal_bars(n["signal"]).strip() or "."))
    idx = g.menu("Select Network", items, status="%d found" % len(nets),
                 show_full=True)
    if idx is None or idx == "QUIT":
        return
    net = nets[idx]
    if net["sec"] == "other":
        g.message("Not supported", ["This network needs", "WPA3, WEP or a", "login (enterprise).",
                                    "WPA2 works."])
        return
    pw = ""
    if net["enc"]:
        pw = g.keyboard("Password") or ""
    g.busy("Connect", ["Connecting to", be.fit(net["ssid"], 24), "(up to ~60s)..."])
    ok = be.connect(net["raw"], pw)
    info = be.status()
    if ok and info.get("connected"):
        be.sync_clock()
        g.message("Connected", [
            "SSID: " + (info.get("ssid") or net["ssid"]),
            "IP:   " + (info.get("ip") or "?"),
            "Time: " + be.local_now(),
            "Saved: joins by itself",
        ])
    else:
        g.message("Failed", ["Could not connect to", be.fit(net["ssid"], 24),
                             "Check the password.", "Log: FunKey/wifi/"])


def screen_status(g):
    g.busy("Status", ["Reading..."])
    info = be.status()
    lines = []
    if info.get("connected"):
        lines.append("State: CONNECTED")
        lines.append("SSID:  " + (info.get("ssid") or "?"))
        lines.append("IP:    " + (info.get("ip") or "?"))
        if info.get("signal"):
            lines.append("Signal: " + info["signal"])
    elif info.get("associated"):
        lines.append("State: GETTING IP")
        lines.append("SSID:  " + (info.get("ssid") or "?"))
    elif info.get("on"):
        lines.append("State: SEARCHING")
        lines.append("Use Scan & Connect.")
    else:
        lines.append("State: WI-FI OFF")
        lines.append("Use Scan & Connect.")
    lines.append("Time:  " + be.local_now())
    g.message("Status", lines)


def screen_sync(g):
    g.busy("Sync Clock", ["Syncing from", "network..."])
    res = be.sync_clock()
    g.message("Sync Clock", ["Now: " + be.local_now(), res[:30]])


# How to get ADB back once Wi-Fi is off: the Connect app, which has USB Link
# built in, says it its own way
USB_HINT = ["for the cable (open", "USB Link for ADB)."]


def screen_disconnect(g):
    g.busy("Wi-Fi", ["Turning Wi-Fi off..."])
    be.disconnect()
    g.message("Wi-Fi", ["Wi-Fi is off.", "The USB port is back"] + USB_HINT)


def screen_on(g):
    if not wifi_on(g):
        return
    g.busy("Wi-Fi", ["Wi-Fi is on.", "Joining a saved", "network..."])
    end = time.time() + 25
    info = be.status()
    while time.time() < end and not info.get("connected"):
        time.sleep(1)
        info = be.status()
    if info.get("connected"):
        g.message("Connected", ["SSID: " + (info.get("ssid") or "?"),
                                "IP:   " + (info.get("ip") or "?")])
    else:
        g.message("Wi-Fi", ["Wi-Fi is on, but no", "saved network is in", "range. Use Scan &", "Connect."])


def screen_about(g):
    g.message("About", [
        "RG Nano Wi-Fi Setup",
        "Dongle: RTL8188FTV",
        "Stays on after Exit",
        "A=ok  B=back",
        "X=space Y=backspace",
    ])


def wifi_tag(info):
    """The Wi-Fi state in a few characters, for a title bar or a menu row."""
    if info.get("connected"):
        return (info.get("ssid") or "online")[:14]
    return "on" if info.get("on") else "off"


def wifi_menu(g, last="Exit"):
    """The Wi-Fi menu, until the user leaves it. Returns 'QUIT' when they asked
    to leave the whole app (Menu/Start), None when they just went back. `last`
    names the bottom row: Exit on its own, Back inside the Connect app."""
    while True:
        info = be.status()
        items = [
            ("Scan & Connect", ""),
            ("Status", ""),
            ("Sync Clock", ""),
            ("Wi-Fi Off" if info.get("on") else "Wi-Fi On", ""),
            ("About", ""),
            (last, ""),
        ]
        choice = g.menu("Wi-Fi Setup", items, status=wifi_tag(info))
        if choice == "QUIT":
            return "QUIT"
        if choice is None or choice == 5:
            return None
        [screen_scan, screen_status, screen_sync,
         screen_disconnect if info.get("on") else screen_on,
         screen_about][choice](g)


def main():
    import signal
    signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    g = Gui()
    try:
        wifi_menu(g)
    except KeyboardInterrupt:
        pass
    finally:
        g.close()


if __name__ == "__main__":
    main()
