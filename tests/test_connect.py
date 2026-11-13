#!/usr/bin/env python3
"""Offline tests for the Connect app (connect/app): no Nano needed.

    python3 tests/test_connect.py

The USB port, the settings folder and the two scripts are stand-ins in a
temporary folder, and the screens are drawn into memory. Needs `sh` for the
stand-in scripts. CONNECT_SHOTS=<dir> also saves every menu and message the
tests draw as a PNG, to look at the screens without a device.
"""
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "wifi", "app"))
sys.path.insert(0, os.path.join(HERE, "..", "connect", "app"))

T = tempfile.mkdtemp(prefix="connect_test_")
GADGET = os.path.join(T, "gadget")
UDCS = os.path.join(T, "udc")
POWER = os.path.join(T, "power")
MUSB = os.path.join(T, "musb")
USBDIR = os.path.join(T, "USB-link")
APPS = os.path.join(T, "Applications")
RUN = os.path.join(T, "run")
CALLS = os.path.join(T, "calls")
FKGPIOD = os.path.join(T, "fkgpiod.conf")


def stub(name, body):
    path = os.path.join(T, name)
    with open(path, "w", newline="\n") as f:
        f.write("#!/bin/sh\n" + body)
    return path


os.environ.update({
    "NANO_FB_HEADLESS": "1", "NANO_FKGPIOD": FKGPIOD,
    "USBLINK_GADGET": GADGET, "USBLINK_UDC_CLASS": UDCS, "USBLINK_POWER": POWER,
    "USBLINK_DIR": USBDIR, "USBLINK_RUN": RUN, "WIFI_MUSB": MUSB, "USBMODE_APPS": APPS,
    "WIFI_DATA": os.path.join(T, "wifi"), "WIFI_RUN": os.path.join(T, "wifi-run"),
    "WIFI_SH": stub("wifi.sh", 'echo "wifi=${STUB_WIFI:-off}"\n'),
    "USBLINK_SH": stub("usblink.sh", 'echo "quiet=$USBLINK_QUIET" >> "%s"\n'
                                      '[ -n "$STUB_RESULT" ] && echo "result: $STUB_RESULT"\n'
                                      'exit "${STUB_EXIT:-0}"\n' % CALLS),
    "USBMODE_SH": stub("usbmode.sh", 'echo "apps=$USBMODE_APPS quiet=$USBMODE_QUIET" >> "%s"\n'
                                      'exit "${STUB_EXIT:-0}"\n' % CALLS),
})
import usb_backend as usb  # noqa: E402
import wifi_gui  # noqa: E402
import connect_gui  # noqa: E402

SHOTS = os.environ.get("CONNECT_SHOTS")


def put(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        f.write(text)


def port(mode="adb", udc_state="configured", power=True, host=False, link="", can_host=True):
    """Stand-in sysfs for one state of the USB port."""
    for d in (GADGET, UDCS, POWER, MUSB, RUN):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(RUN)
    os.makedirs(os.path.join(GADGET, "configs", "FunKey.1"))
    if mode == "adb":
        os.makedirs(os.path.join(GADGET, "functions", "ffs.adb"))
    if link:
        put(os.path.join(GADGET, "functions", link + ".usb0", "ifname"), "usb0\n")
        put(os.path.join(GADGET, "configs", "FunKey.1", link + ".usb0"))
    put(os.path.join(UDCS, "musb-hdrc.1.auto", "state"), udc_state + "\n")
    put(os.path.join(POWER, "online"), "1\n" if power else "0\n")
    put(os.path.join(MUSB, "mode"), "a_host\n" if host else "b_peripheral\n")
    if can_host:
        os.makedirs(os.path.join(MUSB, "usb1"))


def conf(text=None):
    """Write usblink.conf (None: remove it), or read it back with conf.read()."""
    shutil.rmtree(USBDIR, ignore_errors=True)
    if text is not None:
        put(usb.CONF, text)


def conf_text():
    with open(usb.CONF, newline="") as f:
        return f.read()


def calls():
    try:
        with open(CALLS) as f:
            return f.read().split("\n")[:-1]
    except OSError:
        return []


class Base(unittest.TestCase):
    def setUp(self):
        put(FKGPIOD, "MAP A TO KEY KEY_A\nMAP X TO KEY KEY_X\n")
        port()
        conf()
        shutil.rmtree(APPS, ignore_errors=True)
        os.makedirs(APPS)
        if os.path.exists(CALLS):
            os.remove(CALLS)
        for k in ("STUB_RESULT", "STUB_EXIT", "STUB_WIFI"):
            os.environ.pop(k, None)


class Settings(Base):
    def test_read_like_the_script(self):
        conf('# a note\r\nNETWORK=ncm\r\nADDRESS="10.0.0.5/24"\r\n\r\nNETWORK=rndis\r\n lower=x\r\n')
        self.assertEqual(usb.settings(), {"NETWORK": "rndis", "ADDRESS": "10.0.0.5/24"})

    def test_no_file(self):
        self.assertEqual(usb.settings(), {})
        self.assertEqual(usb.plan(), {"network": "no", "internet": "off", "watch": True})

    def test_set_keeps_the_rest(self):
        conf("# my settings\nNETWORK=ncm\n\nSSH=no\nNETWORK=no\n")
        usb.set_settings({"NETWORK": "rndis", "WATCH": "no"})
        self.assertEqual(conf_text(), "# my settings\nNETWORK=rndis\n\nSSH=no\nWATCH=no\n")

    def test_remove(self):
        conf("NETWORK=rndis\nADDRESS=dhcp\nDNS=1.1.1.1\n")
        usb.set_settings({"ADDRESS": None, "GATEWAY": None})
        self.assertEqual(conf_text(), "NETWORK=rndis\nDNS=1.1.1.1\n")

    def test_creates_the_folder(self):
        usb.set_network("ncm")
        self.assertEqual(conf_text(), "NETWORK=ncm\n")


class Plan(Base):
    def test_internet(self):
        conf("NETWORK=rndis\nADDRESS=dhcp\n")
        self.assertEqual(usb.plan()["internet"], "host")
        conf("NETWORK=rndis\nADDRESS=192.168.137.5/24\nGATEWAY=192.168.137.1\n")
        self.assertEqual(usb.plan()["internet"], "custom")
        conf("NETWORK=ncm\n")
        self.assertEqual(usb.plan()["internet"], "off")
        conf("NETWORK=no\nADDRESS=dhcp\n")      # no link: nothing to route over
        self.assertEqual(usb.plan()["internet"], "off")

    def test_unknown_network_is_off(self):
        conf("NETWORK=ecm\nWATCH=no\n")
        self.assertEqual(usb.plan(), {"network": "no", "internet": "off", "watch": False})

    def test_internet_on_windows_sets_dns(self):
        conf("NETWORK=rndis\n")
        usb.set_internet(True)
        self.assertEqual(usb.settings(), {"NETWORK": "rndis", "ADDRESS": "dhcp", "DNS": "1.1.1.1"})

    def test_internet_keeps_a_dns_already_set(self):
        conf("NETWORK=rndis\nDNS=9.9.9.9\n")
        usb.set_internet(True)
        self.assertEqual(usb.settings()["DNS"], "9.9.9.9")

    def test_internet_on_linux_leaves_dns_to_the_host(self):
        conf("NETWORK=ncm\n")
        usb.set_internet(True)
        self.assertEqual(usb.settings(), {"NETWORK": "ncm", "ADDRESS": "dhcp"})

    def test_internet_off_leaves_a_fixed_address(self):
        conf("NETWORK=rndis\nADDRESS=10.1.2.3/24\n")
        usb.set_internet(False)
        self.assertEqual(usb.settings()["ADDRESS"], "10.1.2.3/24")
        conf("NETWORK=rndis\nADDRESS=dhcp\nDNS=1.1.1.1\n")
        usb.set_internet(False)
        self.assertNotIn("ADDRESS", usb.settings())


class State(Base):
    def test_adb_connected(self):
        st = usb.state()
        self.assertEqual((st["mode"], st["connected"], st["host"], st["link"]), ("adb", True, False, ""))
        self.assertEqual(usb.tag(st), "ADB")

    def test_replugged(self):
        port(udc_state="not attached")
        st = usb.state()
        self.assertFalse(st["connected"])
        self.assertEqual(usb.tag(st), "ADB, no host")

    def test_unplugged_is_not_connected(self):
        port(power=False)       # the state file can still say configured
        self.assertFalse(usb.state()["connected"])

    def test_storage(self):
        port(mode="storage")
        self.assertEqual(usb.tag(usb.state()), "storage")

    def test_wifi_has_the_port(self):
        port(host=True)
        st = usb.state()
        self.assertTrue(st["host"])
        self.assertFalse(st["connected"])
        self.assertEqual(usb.tag(st), "Wi-Fi dongle")

    def test_network_link(self):
        port(link="rndis")
        st = usb.state()
        self.assertEqual(st["link"], "rndis")
        self.assertEqual(usb.tag(st), "ADB + net")

    def test_watcher(self):
        self.assertFalse(usb.state()["watching"])
        put(os.path.join(RUN, "usblink-watch.pid"), "%d\n" % os.getpid())
        self.assertTrue(usb.state()["watching"])
        put(os.path.join(RUN, "usblink-watch.pid"), "not a pid\n")
        self.assertFalse(usb.state()["watching"])


class Actions(Base):
    def test_reconnect_result(self):
        os.environ["STUB_RESULT"] = ("connected=1 fellback=0 kind=rndis ip=192.168.137.44 "
                                     "routed=1 dhcp_failed=0 watch=1")
        self.assertEqual(usb.reconnect(), {
            "connected": True, "fellback": False, "kind": "rndis", "ip": "192.168.137.44",
            "routed": True, "dhcp_failed": False, "watch": True})
        self.assertEqual(calls(), ["quiet=1"])     # no on-screen notices over the app

    def test_reconnect_adb_only_has_no_address(self):
        os.environ["STUB_RESULT"] = "connected=0 fellback=0 kind=acm ip=none routed=0 dhcp_failed=0 watch=0"
        res = usb.reconnect()
        self.assertEqual((res["connected"], res["ip"], res["watch"]), (False, "", False))

    def test_reconnect_without_a_result_is_an_error(self):
        os.environ["STUB_EXIT"] = "1"               # "not in ADB mode"
        self.assertEqual(usb.reconnect(), {"error": "exit 1"})

    def test_switch_keeps_the_old_entries_in_step_while_they_are_shown(self):
        put(os.path.join(APPS, "SwitchToUSB.opk"))
        put(os.path.join(APPS, "SwitchToADB.opk.off"))
        self.assertEqual(usb.switch_mode(), 0)
        self.assertEqual(calls(), ["apps=%s quiet=1" % APPS])

    def test_switch_leaves_retired_entries_parked(self):
        put(os.path.join(APPS, "SwitchToUSB.opk.off"))
        put(os.path.join(APPS, "SwitchToADB.opk.off"))
        usb.switch_mode()
        self.assertNotEqual(calls(), ["apps=%s quiet=1" % APPS])
        self.assertTrue(calls()[0].endswith("quiet=1"))

    def test_switch_failure(self):
        os.environ["STUB_EXIT"] = "1"
        self.assertEqual(usb.switch_mode(), 1)


# --------------------------------------------------------------------------- #
# The screens, driven by a list of button presses
# --------------------------------------------------------------------------- #
class Keys:
    """Stand-in for nano_fb.Buttons: plays back a list of presses."""
    queue = []

    def get(self, repeat=True):
        if not Keys.queue:
            raise AssertionError("the app waited for a button after the scripted presses ran out")
        return Keys.queue.pop(0)

    def close(self):
        pass


def png(buf, path):
    rows = b""
    for y in range(240):
        row = bytearray(b"\x00")
        for x in range(240):
            v = buf[(y * 240 + x) * 2] | (buf[(y * 240 + x) * 2 + 1] << 8)
            row += bytes(((v >> 11) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31))
        rows += bytes(row)

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 240, 240, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


class Recorder(wifi_gui.Gui):
    """The real Gui, drawing into memory, that notes every menu and message."""

    def __init__(self, screen=None):
        super().__init__(screen)
        self.seen = []      # ("menu", title, items, sel) / ("message", title, lines)

    def _shot(self):
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            png(self.fb.buf, os.path.join(SHOTS, "%s_%02d.png" % (self.name, len(self.seen))))

    def menu(self, title, items, status="", show_full=False, sel=0):
        self.seen.append(("menu", title, list(items), sel))
        res = super().menu(title, items, status, show_full, sel)
        return res

    def flush(self):
        super().flush()
        self._shot()

    def message(self, title, lines, hint="A/B back", wait=True):
        self.seen.append(("message", title, list(lines)))
        super().message(title, lines, hint, wait)


class Screens(Base):
    def run_hub(self, presses, screen=None):
        Keys.queue = list(presses)
        old = wifi_gui.Buttons
        wifi_gui.Buttons = Keys
        try:
            g = Recorder(screen)
            g.name = self._testMethodName
            connect_gui.hub(g)
            g.close()
        finally:
            wifi_gui.Buttons = old
        self.assertEqual(Keys.queue, [], "presses left over: the app ended early")
        return g.seen

    def menus(self, seen, title):
        return [s for s in seen if s[0] == "menu" and s[1] == title]

    def test_hub_rows(self):
        seen = self.run_hub(["menu"])
        self.assertEqual(seen[0][2], [("Wi-Fi", "off"), ("USB", "ADB"), ("About", ""), ("Exit", "")])
        self.assertEqual(seen[0][3], 0)

    def test_hub_starts_on_usb_after_a_replug(self):
        port(udc_state="not attached")      # cable in, no host talking: what USB Link was opened for
        seen = self.run_hub(["a", "b", "menu"])
        self.assertEqual(seen[0][3], 1)
        self.assertEqual(seen[1][1], "USB")

    def test_handheld_link_row_only_when_bundled(self):
        seen = self.run_hub(["menu"])
        self.assertNotIn(("Handheld Link", ""), seen[0][2])
        bundled = os.path.join(T, "hlink")
        os.makedirs(bundled, exist_ok=True)
        with mock.patch.object(connect_gui, "HLINK_DIR", bundled):
            seen = self.run_hub(["menu"])
        self.assertEqual([r[0] for r in seen[0][2]], ["Wi-Fi", "USB", "Handheld Link", "About", "Exit"])

    def test_handheld_link_takes_over(self):
        bundled = os.path.join(T, "hlink")
        os.makedirs(bundled, exist_ok=True)
        started = []
        with mock.patch.object(connect_gui, "HLINK_DIR", bundled), \
                mock.patch.object(connect_gui, "HLINK_DATA", os.path.join(T, "hlink-data")), \
                mock.patch.object(os, "dup2"), mock.patch.object(os, "chdir"), \
                mock.patch.object(os, "execve", lambda exe, argv, env: started.append((argv[1:], env))):
            self.run_hub(["down", "down", "a", "menu"])
        self.assertEqual(len(started), 1)
        argv, env = started[0]
        self.assertEqual(argv, ["-m", "hlink.main"])
        self.assertEqual(env["PYTHONPATH"], connect_gui.usb.ROOT)   # where hlink/ sits in the OPK

    def test_handheld_link_waits_for_wifi_off(self):
        port(host=True)
        os.environ["STUB_WIFI"] = "on"
        bundled = os.path.join(T, "hlink")
        os.makedirs(bundled, exist_ok=True)
        with mock.patch.object(connect_gui, "HLINK_DIR", bundled), \
                mock.patch.object(os, "execve", side_effect=AssertionError("must not start")):
            seen = self.run_hub(["down", "down", "a", "a", "menu"])
        self.assertIn("Turn Wi-Fi off first.", [s for s in seen if s[0] == "message"][0][2])

    def test_wifi_page_and_back(self):
        seen = self.run_hub(["a", "b", "menu"])
        self.assertEqual(seen[1][1], "Wi-Fi Setup")
        self.assertEqual(seen[1][2][-1], ("Back", ""))
        self.assertEqual(seen[2][1], "Connect")

    def test_usb_page_in_adb_mode(self):
        seen = self.run_hub(["down", "a", "b", "menu"])
        self.assertEqual(self.menus(seen, "USB")[0][2], [
            ("Reconnect", "connected"), ("Network", "off"), ("Auto", "reconnect on"),
            ("Mode", "ADB"), ("Status", ""), ("Back", "")])

    def test_usb_page_in_storage_mode(self):
        port(mode="storage")
        seen = self.run_hub(["down", "a", "b", "menu"])
        self.assertEqual(self.menus(seen, "USB")[0][2],
                         [("Mode", "USB storage"), ("Status", ""), ("Back", "")])

    def test_usb_page_while_wifi_has_the_port(self):
        port(host=True)
        seen = self.run_hub(["down", "a", "a", "menu"])
        self.assertEqual(seen[1][0], "message")
        self.assertIn("by the Wi-Fi dongle.", seen[1][2])
        self.assertEqual(calls(), [])

    def test_settings_from_the_device(self):
        # USB, Network (-> Windows), Internet (-> via host), then leave the app
        seen = self.run_hub(["down", "a", "down", "a", "down", "a", "menu"])
        self.assertEqual(usb.settings(), {"NETWORK": "rndis", "ADDRESS": "dhcp", "DNS": "1.1.1.1"})
        last = self.menus(seen, "USB")[-1]
        self.assertEqual(last[2][:3], [("Apply", "reconnect now"), ("Network", "Windows"),
                                       ("Internet", "via host")])
        self.assertEqual(last[3], 2)            # the cursor stays on the row just changed
        self.assertEqual(calls(), [])           # nothing reconnects until asked

    def test_network_cycles(self):
        self.run_hub(["down", "a", "down", "a", "a", "a", "menu"])
        self.assertEqual(usb.settings()["NETWORK"], "no")

    def test_custom_gateway_is_left_alone(self):
        text = "NETWORK=rndis\nADDRESS=192.168.137.5/24\nGATEWAY=192.168.137.1\nDNS=1.1.1.1\n"
        conf(text)
        seen = self.run_hub(["down", "a", "down", "down", "a", "a", "menu"])
        self.assertEqual(conf_text(), text)
        self.assertEqual(seen[-2][1], "Internet")

    def test_reconnect_shows_the_result(self):
        os.environ["STUB_RESULT"] = ("connected=1 fellback=0 kind=rndis ip=192.168.137.44 "
                                     "routed=1 dhcp_failed=0 watch=1")
        seen = self.run_hub(["down", "a", "a", "a", "menu"])
        msg = [s for s in seen if s[0] == "message"][0]
        self.assertEqual(msg[2], ["Connected: ADB", "Network: Windows", "IP: 192.168.137.44",
                                  "Internet via the host", "Auto-reconnect on"])
        self.assertEqual(calls(), ["quiet=1"])

    def test_reconnect_no_host(self):
        os.environ["STUB_RESULT"] = "connected=0 fellback=0 kind=acm ip=none routed=0 dhcp_failed=0 watch=1"
        seen = self.run_hub(["down", "a", "a", "a", "menu"])
        self.assertEqual([s for s in seen if s[0] == "message"][0][2][0], "No host yet.")

    def test_mode_switch_asks_first(self):
        # USB, down to Mode, A, then B at the question: nothing switches
        seen = self.run_hub(["down", "a", "down", "down", "down", "a", "b", "menu"])
        self.assertEqual([s for s in seen if s[0] == "message"][0][2][0], "Switch to USB storage?")
        self.assertEqual(calls(), [])

    def test_mode_switch_failure_is_shown(self):
        os.environ["STUB_EXIT"] = "1"
        seen = self.run_hub(["down", "a", "down", "down", "down", "a", "a", "a", "menu"])
        self.assertEqual([s for s in seen if s[0] == "message"][-1][2][0], "Could not switch:")
        self.assertEqual(len(calls()), 1)

    def test_no_dhcp_says_what_to_do_on_the_pc(self):
        os.environ["STUB_RESULT"] = ("connected=1 fellback=0 kind=rndis ip=169.254.170.5 "
                                     "routed=0 dhcp_failed=1 watch=1")
        seen = self.run_hub(["down", "a", "a", "a", "menu"])
        self.assertIn("Remote NDIS adapter.", [s for s in seen if s[0] == "message"][0][2])

    def test_every_line_fits_the_screen(self):
        os.environ["STUB_RESULT"] = ("connected=1 fellback=0 kind=rndis ip=192.168.137.244 "
                                     "routed=0 dhcp_failed=1 watch=1")
        port(link="rndis")
        seen = self.run_hub(["down", "a", "a", "a",                 # reconnect result
                             "down", "down", "down", "down", "a", "a",   # status
                             "b", "down", "a", "a", "menu"])        # about
        fb = wifi_gui.Framebuffer()
        for s in seen:
            if s[0] == "message":
                for line in s[2]:
                    self.assertLessEqual(fb.text_width(line), 240 - 12, "too wide at 1x: %r" % line)

    def test_funkey_s_hides_wifi_and_detects_model(self):
        port(can_host=False)
        self.assertEqual(usb.model(), "FunKey S")
        seen = self.run_hub(["down", "a", "a", "menu"])
        self.assertEqual([r[0] for r in seen[0][2]], ["USB", "About", "Exit"])
        self.assertEqual(seen[1][2][0], "Connect: FunKey S")

    def test_gba_mini_window_and_layout(self):
        put(FKGPIOD, "MAP START+A TO KEY KEY_X\nMAP START+B TO KEY KEY_Y\n")
        port(can_host=False, link="rndis")
        self.assertEqual(usb.model(), "GBA Mini")
        self.assertEqual(wifi_gui.Framebuffer().h, 160)
        os.environ["STUB_RESULT"] = ("connected=1 fellback=0 kind=rndis ip=169.254.170.5 "
                                     "routed=0 dhcp_failed=1 watch=1")
        # Open USB -> Reconnect (6-line message on 160px), then About -> Exit
        Keys.queue = ["a", "a", "a", "b", "down", "a", "a", "menu"]
        old = wifi_gui.Buttons
        wifi_gui.Buttons = Keys
        try:
            g = Recorder()
            g.name = self._testMethodName
            connect_gui.hub(g)
            # Off-screen rows (0..47 and 208..239) must remain black on GBA Mini
            top_border = bytes(g.fb.buf[:48 * 480])
            bot_border = bytes(g.fb.buf[208 * 480:])
            self.assertEqual(top_border, b"\x00" * len(top_border))
            self.assertEqual(bot_border, b"\x00" * len(bot_border))
            self.assertNotEqual(bytes(g.fb.buf[48 * 480:208 * 480]), b"\x00" * (160 * 480))
            # Message height must stay above footer on 240x160
            y_end = g._draw_lines(["Connected: ADB", "Network: Windows", "IP: 169.254.170.5",
                                   "No DHCP: no internet.", "On the PC, share its",
                                   "Auto-reconnect on"], g.title_h + 6, wifi_gui.Pal.HI)
            self.assertLessEqual(y_end, g.h - g.foot_h)
            g.close()
        finally:
            wifi_gui.Buttons = old
        self.assertEqual(Keys.queue, [])
        self.assertEqual(g.seen[0][2][0], ("USB", "ADB + net"))
        self.assertEqual([s for s in g.seen if s[0] == "message"][-1][2][0], "Connect: GBA Mini")

    def test_gba_mini_present_power_counts_as_connected(self):
        put(FKGPIOD, "MAP START+A TO KEY KEY_X\nMAP START+B TO KEY KEY_Y\n")
        port(can_host=False, power=False, udc_state="configured", link="rndis")
        self.assertFalse(usb.state()["connected"])
        put(os.path.join(POWER, "present"), "1\n")
        st = usb.state()
        self.assertTrue(st["power"])
        self.assertTrue(st["connected"])
        self.assertEqual(usb.tag(st), "ADB + net")


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        shutil.rmtree(T, ignore_errors=True)
