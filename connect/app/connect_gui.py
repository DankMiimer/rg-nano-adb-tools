#!/usr/bin/env python3
"""
Connect -- one app for the RG Nano's Wi-Fi and USB connections.

A hub with two pages:
  Wi-Fi  the Wi-Fi Setup screens (wifi_gui.py), unchanged
  USB    USB Link (reconnect, network link, the host's internet) and USB Mode
         (ADB or USB storage), with the settings that used to need a file
         edited on the PC

The work is done by the same scripts the separate apps run (wifi.sh,
usblink.sh, usbmode.sh); wifi_backend.py and usb_backend.py drive them.
"""
import os
import sys
import time

import ra_backend
import ra_gui
import usb_backend as usb
import wifi_backend as wifi
import wifi_gui

Gui = wifi_gui.Gui

# what "Wi-Fi Off" says about getting ADB back
wifi_gui.USB_HINT = ["for the cable: USB,", "then Reconnect."]

INTERNET_NAMES = {"off": "off", "host": "via host", "custom": "custom"}

# Handheld Link (file sharing with another handheld, from the handheld-usb-link
# project), if the OPK was built with it: its Python package sits next to app/
HLINK_DIR = os.path.join(usb.ROOT, "hlink")
HLINK_DATA = os.environ.get("HLINK_DATA", "/mnt/FunKey/hlink")


# --------------------------------------------------------------------------- #
# USB page
# --------------------------------------------------------------------------- #
def usb_reconnect(g):
    g.busy("USB", ["Reconnecting USB...", "(up to ~30s)"])
    res = usb.reconnect()
    if "error" in res:
        g.message("USB", ["Could not reconnect.", "Log: FunKey/usblink.log", res["error"][:28]])
        return
    if not res["connected"]:
        if res["watch"]:
            g.message("USB", ["No host yet.", "Plug the cable in:", "it connects by itself."])
        else:
            g.message("USB", ["No host found.", "Plug the cable in", "and try again."])
        return
    lines = ["Connected: ADB"]
    if res["fellback"]:
        lines += ["Network link failed,", "so ADB only."]
    elif res["kind"] != "acm":
        lines.append("Network: " + usb.NETWORK_NAMES.get(res["kind"], res["kind"]))
        if res["ip"]:
            lines.append("IP: " + res["ip"])
        if res["dhcp_failed"]:
            if res["kind"] == "rndis":
                lines += ["No DHCP: no internet.", "On the PC, share its", "internet with the",
                          "Remote NDIS adapter."]
            else:
                lines += ["No DHCP: no internet.", "The host has to share", "its connection."]
        elif res["routed"]:
            lines.append("Internet via the host")
    if res["watch"]:
        lines.append("Auto-reconnect on")
    g.message("USB", lines)


def usb_switch_mode(g, st, title="USB mode", ask=None):
    to_storage = st["mode"] == "adb"
    if ask is None:
        if to_storage:
            ask = ["Switch to USB storage?", "The Nano reboots.", "ADB stops; Mount USB",
                   "then shares the card."]
        else:
            ask = ["Switch to ADB?", "The Nano reboots.", "The USB drive is not", "there in ADB mode."]
    if not g.confirm(title, ask):
        return
    g.busy(title, ["Switching to", "storage..." if to_storage else "ADB...", "Rebooting."])
    if usb.switch_mode() != 0:
        g.message(title, ["Could not switch:", "cannot write to the", "SD card (/mnt)."])
        return
    while True:             # the Nano is going down; keep the notice up
        time.sleep(5)


def usb_status(g):
    g.busy("USB", ["Reading..."])
    st = usb.state()
    cfg = usb.plan()
    lines = ["Mode: " + ("ADB" if st["mode"] == "adb" else "USB storage")]
    if st["host"]:
        lines.append("Port: Wi-Fi dongle")
    else:
        lines.append("Host: " + ("connected" if st["connected"] else
                                 "none (power)" if st["power"] else "no cable"))
    if st["mode"] == "adb":
        if st["link"]:
            lines.append("Link: " + usb.NETWORK_NAMES[st["link"]])
            lines.append("IP: " + (st["ip"] or "none yet"))
        lines.append("Auto: " + ("running" if st["watching"] else
                                 "on, not started" if cfg["watch"] else "off"))
    g.message("USB", lines)


def usb_menu(g):
    """The USB page, until the user leaves it. Returns 'QUIT' or None, like
    wifi_gui.wifi_menu."""
    dirty = False       # settings changed since the last reconnect
    sel = 0
    while True:
        st = usb.state()
        if st["host"]:
            g.message("USB", ["The USB port is in use", "by the Wi-Fi dongle.", "Turn Wi-Fi off to use", "the cable."])
            return None
        cfg = usb.plan()
        rows = []       # (label, detail, action)
        if st["mode"] == "adb":
            rows.append(("Apply" if dirty else "Reconnect",
                         "reconnect now" if dirty else ("connected" if st["connected"] else "no host"),
                         "reconnect"))
            rows.append(("Network", usb.NETWORK_NAMES[cfg["network"]], "network"))
            if cfg["network"] != "no":
                rows.append(("Internet", INTERNET_NAMES[cfg["internet"]], "internet"))
            rows.append(("Auto", "reconnect " + ("on" if cfg["watch"] else "off"), "watch"))
            rows.append(("Mode", "ADB", "mode"))
        else:
            rows.append(("Mode", "USB storage", "mode"))
        rows.append(("Status", "", "status"))
        rows.append(("Back", "", "back"))
        choice = g.menu("USB", [(r[0], r[1]) for r in rows], status=usb.tag(st), sel=sel)
        if choice == "QUIT":
            return "QUIT"
        if choice is None or rows[choice][2] == "back":
            if dirty and g.confirm("USB", ["Apply your new USB", "settings now?"]):
                usb_reconnect(g)
            return None
        sel = choice
        action = rows[choice][2]
        if action == "reconnect":
            usb_reconnect(g)
            dirty = False
        elif action == "network":
            order = usb.NETWORKS
            usb.set_network(order[(order.index(cfg["network"]) + 1) % len(order)])
            dirty = True
        elif action == "internet":
            if cfg["internet"] == "custom":
                g.message("Internet", ["A fixed GATEWAY is set", "in USB-link/", "usblink.conf.", "Change it there."])
            else:
                usb.set_internet(cfg["internet"] == "off")
                dirty = True
        elif action == "watch":
            usb.set_watch(not cfg["watch"])
            dirty = True
        elif action == "mode":
            usb_switch_mode(g, st)
        elif action == "status":
            usb_status(g)


# --------------------------------------------------------------------------- #
# Hub
# --------------------------------------------------------------------------- #
def screen_about(g):
    if usb.can_host():
        lines = [
            "Connect: " + usb.model(),
            "Wi-Fi dongle: RTL8188FTV",
            "One USB port: the dongle",
            "or the cable, not both.",
            "Start/Menu: leave the app",
        ]
    else:
        lines = [
            "Connect: " + usb.model(),
            "USB Link and USB mode",
            "USB port: device only",
            "Start/Menu: leave the app",
        ]
    g.message("About", lines)


def has_hlink():
    return os.path.isdir(HLINK_DIR)


def start_hlink(g):
    """Hand the screen over to Handheld Link. It replaces this app instead of
    running beside it: the Nano has 64 MB, and two Pythons with a file copy
    going is one too many. Leaving Handheld Link goes back to the menu."""
    st = usb.state()
    if st["host"] and wifi.status().get("on"):
        g.message("Handheld Link", ["The USB port is in use", "by the Wi-Fi dongle.", "Turn Wi-Fi off first."])
        return
    if st["mode"] == "storage":
        usb_switch_mode(g, st, title="Handheld Link",
                        ask=["Needs ADB mode.", "Switch to ADB now?", "The device reboots."])
        return
    msg = "Preparing USB link..." if (not st["host"] and st["link"]) else "Starting..."
    g.busy("Handheld Link", [msg])
    usb.prepare_hlink()
    g.close()
    env = dict(os.environ, PYTHONPATH=usb.ROOT)
    env.pop("NANO_NO_VT", None)
    try:
        os.makedirs(HLINK_DATA, exist_ok=True)
        fd = os.open(os.path.join(HLINK_DATA, "stderr.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.dup2(fd, 2)
    except OSError:
        pass
    os.chdir("/")
    os.execve(sys.executable, [sys.executable, "-m", "hlink.main"], env)


def hub(g):
    first = True
    sel = 0
    while True:
        host_cap = usb.can_host()
        info = wifi.status() if host_cap else {}
        st = usb.state()
        rows = []
        if host_cap:
            rows.append(("Wi-Fi Dongle", wifi_gui.wifi_tag(info), "wifi"))
        rows.append(("USB", usb.tag(st), "usb"))
        if has_hlink():
            rows.append(("Handheld Link", "", "hlink"))
        rows += [
            ("Achievements", ra_backend.hub_tag(), "ra"),
            ("About", "", "about"),
            ("Exit", "", "exit"),
        ]
        if first:
            # Opened with a cable in and no host talking to it: that is what
            # USB Link was opened for, so start on USB. Otherwise, if unlocks
            # are waiting to sync, start directly on Achievements.
            if host_cap and st["mode"] == "adb" and st["power"] and not st["connected"] and not st["host"]:
                sel = 1
            elif rows[min(sel, len(rows) - 1)][2] != "ra" and ra_backend.hub_tag().endswith("pending"):
                for idx, row in enumerate(rows):
                    if row[2] == "ra":
                        sel = idx
                        break
            first = False
        choice = g.menu("Connect", [(r[0], r[1]) for r in rows], status=usb.model(), sel=sel)
        if choice in (None, "QUIT"):
            return
        sel = choice
        action = rows[choice][2]
        res = None
        if action == "exit":
            return
        if action == "wifi":
            res = wifi_gui.wifi_menu(g, last="Back")
        elif action == "usb":
            res = usb_menu(g)
        elif action == "hlink":
            start_hlink(g)
        elif action == "ra":
            res = ra_gui.ra_menu(g)
        else:
            screen_about(g)
        if res == "QUIT":
            return


def main():
    g = Gui()
    try:
        hub(g)
    except KeyboardInterrupt:
        pass
    finally:
        g.close()


if __name__ == "__main__":
    main()
