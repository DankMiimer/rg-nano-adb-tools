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
from nano_font import GLYPH_H, UI_FONT, UI_GLYPH_H
import wifi_backend as be  # backend: turn_on / scan / status / connect / disconnect / sync_clock

# Native crisp UI font (18px Terminus Bold proportional) for headers & menu rows;
# compact 8x14 font at 1x for dense cards, badges, and keyboard grid.
_UI = (UI_FONT, UI_GLYPH_H)
BS = 2                  # legacy alias for input scaling on 240x240 keyboard
BIG = UI_GLYPH_H
TITLE_H = 0
FOOT_H = 0
ROWH = UI_GLYPH_H + 14
LINE = GLYPH_H + 4

KB_ROWS = [
    list("qwertyuiop"),
    list("asdfghjkl"),
    list("zxcvbnm"),
    list("0123456789"),
    list(".-_@!#$%&*"),
    ["aA", "SPC", "DEL", "OK"],
]

# Crisp 10x10 pixel icons for menu rows (MSB-left, 10 bits per row)
ICON_W = 10
ICONS = {
    "wifi": (
        0b0000000000,
        0b0111111110,
        0b1100000011,
        0b0011111100,
        0b0110000110,
        0b0001111000,
        0b0010000100,
        0b0000110000,
        0b0000110000,
        0b0000000000,
    ),
    "usb": (
        0b0000110000,
        0b0001111000,
        0b0000110000,
        0b0110110110,
        0b0110110110,
        0b0100110010,
        0b0011111100,
        0b0000110000,
        0b0000110000,
        0b0001111000,
    ),
    "link": (
        0b0000000000,
        0b0011110000,
        0b0110011000,
        0b0110001110,
        0b0011001011,
        0b1101001100,
        0b0111000110,
        0b0001100110,
        0b0000111100,
        0b0000000000,
    ),
    "trophy": (
        0b1111111111,
        0b1011111101,
        0b1011111101,
        0b1111111111,
        0b0011111100,
        0b0001111000,
        0b0000110000,
        0b0000110000,
        0b0011111100,
        0b0011111100,
    ),
    "sync": (
        0b0011111000,
        0b0110001101,
        0b1100000111,
        0b1100000011,
        0b0000000000,
        0b0000000000,
        0b1100000011,
        0b1110000011,
        0b1011000110,
        0b0001111100,
    ),
    "queue": (
        0b0000000000,
        0b1101111111,
        0b1101111111,
        0b0000000000,
        0b1101111111,
        0b1101111111,
        0b0000000000,
        0b1101111111,
        0b1101111111,
        0b0000000000,
    ),
    "scan": (
        0b0011110000,
        0b0110011000,
        0b1100001100,
        0b1100001100,
        0b0110011000,
        0b0011111000,
        0b0000011100,
        0b0000001110,
        0b0000000111,
        0b0000000010,
    ),
    "user": (
        0b0001111000,
        0b0011111100,
        0b0011111100,
        0b0011111100,
        0b0001111000,
        0b0000000000,
        0b0111111110,
        0b1111111111,
        0b1111111111,
        0b0000000000,
    ),
    "history": (
        0b0011111100,
        0b0110000110,
        0b1100110011,
        0b1100110011,
        0b1100111111,
        0b1100000011,
        0b1100000011,
        0b0110000110,
        0b0011111100,
        0b0000000000,
    ),
    "info": (
        0b0011111100,
        0b0100110010,
        0b1000110001,
        0b1000000001,
        0b1001110001,
        0b1000110001,
        0b1000110001,
        0b0101111010,
        0b0011111100,
        0b0000000000,
    ),
    "back": (
        0b0001100000,
        0b0011100000,
        0b0111111110,
        0b1111111111,
        0b1111111111,
        0b0111111110,
        0b0011100000,
        0b0001100000,
        0b0000000000,
        0b0000000000,
    ),
}

_LABEL_ICON = {
    "wi-fi": "wifi",
    "wi-fi dongle": "wifi",
    "wi-fi on": "wifi",
    "wi-fi off": "wifi",
    "scan & connect": "scan",
    "usb": "usb",
    "reconnect": "sync",
    "apply": "sync",
    "network": "usb",
    "internet": "wifi",
    "auto": "sync",
    "mode": "usb",
    "handheld link": "link",
    "achievements": "trophy",
    "sync now": "sync",
    "pending queue": "queue",
    "scan roms": "scan",
    "all systems": "scan",
    "account": "user",
    "sign in": "user",
    "sign out": "user",
    "import login.txt": "user",
    "history": "history",
    "status": "info",
    "about": "info",
    "sync clock": "history",
    "back": "back",
    "exit": "back",
}


def _badge_colors(tag: str) -> tuple[int, int, int]:
    """Return (fg, bg, border) semantic colors for a status badge."""
    low = (tag or "").strip().lower()
    if any(k in low for k in ("no dhcp", "no host", "dongle", "fail", "err")):
        return Pal.ERR, Pal.ERR_BG, Pal.ERR
    if any(k in low for k in ("pending", "reconnect now", "sign in", "checking", "getting ip", "search")) or low.endswith(" up"):
        return Pal.WARN, Pal.WARN_BG, Pal.WARN
    if low in ("ready", "connected", "on", "running", "adb + net", "adb", "via host", "reconnect on", "windows", "linux"):
        return Pal.OK, Pal.OK_BG, Pal.OK
    if low in ("off", "storage", "reconnect off", "none", "run", "never"):
        return Pal.DIM, Pal.BG2, Pal.BORDER
    return Pal.INK, Pal.CARD, Pal.BORDER


class Gui:
    def __init__(self, screen=None):
        self.fb = Framebuffer(screen)
        self.btns = Buttons()
        self.fb.enter_graphics()
        self.w = self.fb.w
        self.h = self.fb.h
        compact = self.h < 200
        self.title_h = 0
        self.title_y = 0
        self.foot_h = 0
        self.rowh = 25 if compact else 32

    def close(self):
        try:
            self.fb.leave_graphics()
            self.fb.close()
            self.btns.close()
        except Exception:
            pass

    # ----- chrome ------------------------------------------------------- #
    def header(self, title, status=""):
        self.fb.fill(Pal.BG)

    def footer(self, hint, bar_h=GLYPH_H + 4):
        fb = self.fb
        fb.fill_rect(0, self.h - bar_h, self.w, bar_h, Pal.BG2)
        fb.fill_rect(0, self.h - bar_h, self.w, 1, Pal.BORDER)
        x = max(6, (self.w - fb.text_width(hint)) // 2)
        fb.text(x, self.h - bar_h + 2, hint, Pal.DIM, 1)

    def flush(self):
        self.fb.flush()

    def _draw_card(self):
        compact = self.h < 200
        mx = 4 if compact else 6
        top = 4 if compact else 8
        bot = self.h - self.foot_h - (4 if compact else 8)
        if bot > top + 8:
            self.fb.fill_pill(mx, top, self.w - mx * 2, bot - top, Pal.CARD, Pal.BORDER)

    def _draw_progress_bar(self, done: int, total: int, reserve_bot: int = 0):
        if total <= 0:
            return
        fb = self.fb
        compact = self.h < 200
        bar_h = 10 if compact else 12
        bar_y = self.h - self.foot_h - reserve_bot - bar_h - (10 if compact else 14)
        bar_x = 14
        bar_w = self.w - 28
        pct = min(100, max(0, (done * 100) // total))
        lbl = f"{pct}% ({done}/{total})"
        fb.text((self.w - fb.text_width(lbl)) // 2, bar_y - GLYPH_H - 4, lbl, Pal.HI, 1)
        fb.fill_pill(bar_x, bar_y, bar_w, bar_h, Pal.BG, Pal.BORDER)
        fill_w = ((bar_w - 4) * min(done, total)) // total
        if fill_w > 0:
            fb.fill_rect(bar_x + 2, bar_y + 2, fill_w, bar_h - 4, Pal.ACCENT)

    def _draw_lines(self, lines, y0, color, reserve_bot: int = 0):
        """Draw stacked card text using UI_FONT for short prompts and crisp 8x14
        when lines are dense, always staying above footer/reserved area."""
        compact = self.h < 200
        bottom_pad = (6 if compact else 10) + reserve_bot
        avail_h = self.h - self.foot_h - y0 - bottom_pad
        max_w = self.w - 28
        use_ui = bool(lines) and len(lines) <= 4 and all(
            self.fb.text_width(ln, fnt=_UI) <= max_w for ln in lines
        )
        if use_ui:
            gap = 4 if compact else 6
            need = len(lines) * UI_GLYPH_H + max(0, len(lines) - 1) * gap
            if need <= avail_h:
                y = y0 + (2 if compact else 4)
                for ln in lines:
                    self.fb.text(14, y, ln, color, 1, fnt=_UI)
                    y += UI_GLYPH_H + gap
                return y
        n = len(lines)
        gap = 4 if compact else 6
        while gap > 0 and (n * GLYPH_H + max(0, n - 1) * gap) > avail_h:
            gap -= 1
        top_extra = 0 if (n * GLYPH_H + max(0, n - 1) * gap + 4) > avail_h else (2 if compact else 4)
        y = y0 + top_extra
        for ln in lines:
            self.fb.text(14, y, ln, color, 1)
            y += GLYPH_H + gap
        return y

    def busy(self, title, lines, hint="", progress=None):
        self.header(title)
        self._draw_card()
        y0 = 8 if self.h < 200 else 14
        reserve = (GLYPH_H + 8 if hint else 0) + (34 if progress is not None else 0)
        self._draw_lines(lines, y0, Pal.INK, reserve_bot=reserve)
        if progress is not None:
            self._draw_progress_bar(progress[0], progress[1],
                                    reserve_bot=(GLYPH_H + 8 if hint else 0))
        if hint:
            hx = max(10, (self.w - self.fb.text_width(hint)) // 2)
            hy = self.h - self.foot_h - GLYPH_H - (9 if self.h < 200 else 12)
            self.fb.text(hx, hy, hint, Pal.DIM, 1)
        self.flush()

    def check_cancel(self) -> bool:
        """Non-blocking check whether the user pressed B / Menu / Start to cancel."""
        poll = getattr(self.btns, "poll", None)
        if not poll:
            return False
        while True:
            k = poll(repeat=False)
            if k is None:
                return False
            if k in ("b", "menu", "start"):
                return True

    def _draw_confirm_buttons(self):
        fb = self.fb
        compact = self.h < 200
        btn_h = GLYPH_H + 6
        btn_y = self.h - btn_h - (10 if compact else 14)
        gap = 12
        bw = (self.w - 36 - gap) // 2
        x1 = 18
        x2 = x1 + bw + gap
        yes_lbl = "A  Yes"
        no_lbl = "B  No"
        fb.fill_pill(x1, btn_y, bw, btn_h, Pal.OK_BG, Pal.OK)
        fb.text(x1 + (bw - fb.text_width(yes_lbl)) // 2, btn_y + 3, yes_lbl, Pal.OK, 1)
        fb.fill_pill(x2, btn_y, bw, btn_h, Pal.BG2, Pal.BORDER)
        fb.text(x2 + (bw - fb.text_width(no_lbl)) // 2, btn_y + 3, no_lbl, Pal.INK, 1)

    def message(self, title, lines, hint="A/B back", wait=True):
        self.header(title)
        self._draw_card()
        is_confirm = (hint == "confirm")
        y0 = 8 if self.h < 200 else 14
        reserve = GLYPH_H + 18 if is_confirm else 0
        self._draw_lines(lines, y0, Pal.HI, reserve_bot=reserve)
        if is_confirm:
            self._draw_confirm_buttons()
        self.flush()
        if wait:
            self.wait_any()

    def wait_any(self):
        while True:
            if self.btns.get() in ("a", "b", "menu", "start"):
                return

    def confirm(self, title, lines):
        """A yes/no question: True for A, False for B (or Menu/Start)."""
        self.message(title, lines, hint="confirm", wait=False)
        while True:
            k = self.btns.get(repeat=False)
            if k == "a":
                return True
            if k in ("b", "menu", "start"):
                return False

    def _clip(self, s, max_px):
        """Truncate UI-font text to max_px, adding '..' if cut."""
        if self.fb.text_width(s, fnt=_UI) <= max_px:
            return s
        while s and self.fb.text_width(s + "..", fnt=_UI) > max_px:
            s = s[:-1]
        return s + ".."

    # ----- menu / list -------------------------------------------------- #
    def menu(self, title, items, status="", show_full=False, sel=0):
        """items: list of (label, detail). Returns index, None (back), or 'QUIT'.
        show_full: render the selected item's full label in a compact bottom
        strip (for long SSIDs that get truncated in the row). sel: starting row."""
        sel = max(0, min(sel, len(items) - 1))
        off = 0
        compact = self.h < 200
        preview_h = (GLYPH_H + 4) if (show_full and items) else 0
        top_pad = 4 if compact else 8
        avail = max(1, (self.h - self.title_h - top_pad - preview_h) // self.rowh)
        while True:
            if sel < off:
                off = sel
            if sel >= off + avail:
                off = sel - avail + 1
            self.header(title, status)
            fb = self.fb
            y = self.title_h + top_pad
            need_sb = len(items) > avail
            row_x = 4 if compact else 6
            row_r = self.w - (9 if need_sb else row_x)
            row_w = row_r - row_x
            row_box_h = self.rowh - 2
            sel_clipped = False
            for i in range(off, min(len(items), off + avail)):
                lab, det = items[i]
                selected = (i == sel)
                if selected:
                    fb.fill_pill(row_x, y, row_w, row_box_h, Pal.SEL, Pal.ACCENT)
                    fb.fill_rect(row_x, y + 2, 3, row_box_h - 4, Pal.ACCENT)
                else:
                    fb.fill_pill(row_x, y, row_w, row_box_h, Pal.CARD, None)
                ink = Pal.HI if selected else Pal.INK
                icon_key = _LABEL_ICON.get(lab.strip().lower())
                lx = row_x + 8
                if icon_key:
                    fb.bitmap(lx, y + (row_box_h - ICON_W) // 2, ICONS[icon_key],
                              Pal.ACCENT if selected else Pal.DIM, width=ICON_W)
                    lx += ICON_W + 6
                det_w = fb.text_width(det) if det else 0
                pill_w = det_w + 8 if det else 0
                if det:
                    fg, bg, bd = _badge_colors(det)
                    ph = GLYPH_H + 4
                    px = row_r - pill_w - 5
                    py = y + max(0, (row_box_h - ph) // 2)
                    fb.fill_pill(px, py, pill_w, ph, bg, bd)
                    fb.text(px + 4, py + 2, det, fg, 1)
                label_max = row_r - lx - (pill_w + 10 if det else 6)
                clipped = self._clip(lab, label_max)
                if selected:
                    sel_clipped = (clipped != lab)
                ty = y + max(0, (row_box_h - UI_GLYPH_H) // 2)
                fb.text(lx, ty, clipped, ink, 1, fnt=_UI)
                y += self.rowh
            if need_sb:
                sb_y = self.title_h + top_pad
                sb_h = max(10, self.h - preview_h - sb_y - 4)
                fb.fill_rect(self.w - 5, sb_y, 2, sb_h, Pal.BG2)
                thumb_h = max(8, sb_h * avail // len(items))
                thumb_y = sb_y + (sb_h - thumb_h) * off // max(1, len(items) - avail)
                fb.fill_rect(self.w - 5, thumb_y, 2, thumb_h, Pal.ACCENT)
            if preview_h and sel_clipped:
                self.footer(items[sel][0], bar_h=preview_h)
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
        kb_foot = GLYPH_H + 4
        in_fnt = None if compact else _UI
        in_h = (GLYPH_H if compact else UI_GLYPH_H) + 6
        while True:
            r = max(0, min(r, len(KB_ROWS) - 1))
            c = max(0, min(c, len(KB_ROWS[r]) - 1))
            fb = self.fb
            self.header(title, "ABC" if shift else "abc")
            # input field
            in_y = 6 if compact else 8
            fb.fill_pill(6, in_y, self.w - 12, in_h, Pal.BG, Pal.BORDER)
            mode_lbl = "ABC" if shift else "abc"
            mw = fb.text_width(mode_lbl) + 8
            mh = GLYPH_H + 4
            mx = self.w - mw - 10
            my = in_y + max(1, (in_h - mh) // 2)
            fb.fill_pill(mx, my, mw, mh, Pal.BG2, Pal.BORDER)
            fb.text(mx + 4, my + 2, mode_lbl, Pal.ACCENT if shift else Pal.DIM, 1)
            shown = text
            max_in_w = mx - 16
            while shown and fb.text_width(shown + "_", fnt=in_fnt) > max_in_w:
                shown = shown[1:]
            if shown:
                fb.text(10, in_y + 2, shown + "_", Pal.HI, 1, fnt=in_fnt)
            else:
                fb.text(10, in_y + 2, "_", Pal.HI, 1, fnt=in_fnt)
                fb.text(10 + fb.text_width("_ ", fnt=in_fnt), in_y + 2, title, Pal.DIM, 1, fnt=in_fnt)
            # key grid (1x so 6 rows fit; keys are single chars, still readable)
            top = in_y + in_h + (4 if compact else 6)
            gh = self.h - top - kb_foot - 2
            cellh = gh // len(KB_ROWS)
            for ri, row in enumerate(KB_ROWS):
                cellw = (self.w - 8) // len(row)
                for ci, key in enumerate(row):
                    kx = 4 + ci * cellw
                    ky = top + ri * cellh
                    seld = (ri == r and ci == c)
                    fb.fill_pill(kx + 1, ky + 1, cellw - 2, cellh - 2,
                                 Pal.ACCENT if seld else Pal.BG2)
                    lab = key.upper() if (shift and len(key) == 1) else key
                    fb.text(kx + (cellw - fb.text_width(lab)) // 2,
                            ky + (cellh - GLYPH_H) // 2, lab,
                            Pal.BLACK if seld else Pal.INK, 1)
            self.footer("A type  B done  X spc  Y del", bar_h=kb_foot)
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
        choice = g.menu("Wi-Fi Dongle", items, status=wifi_tag(info))
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
