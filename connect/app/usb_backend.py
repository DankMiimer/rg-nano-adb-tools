"""
USB backend for the Connect app: what the USB port is doing right now, the
settings in usblink.conf, and the two scripts that change things (usblink.sh
rebuilds the USB connection, usbmode.sh switches between ADB and USB storage
with a reboot).

The scripts stay the engines, exactly as their own OPKs run them; this only
reads state, edits the settings file and shows their result.
"""
from __future__ import annotations

import glob
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
USBLINK_SH = os.environ.get("USBLINK_SH", os.path.join(ROOT, "usblink.sh"))
USBMODE_SH = os.environ.get("USBMODE_SH", os.path.join(ROOT, "usbmode.sh"))
# the same test hooks as the scripts
GADGET = os.environ.get("USBLINK_GADGET", "/sys/kernel/config/usb_gadget/FunKey")
UDCS = os.environ.get("USBLINK_UDC_CLASS", "/sys/class/udc")
DIR = os.environ.get("USBLINK_DIR", "/mnt/USB-link")
POWER = os.environ.get("USBLINK_POWER", "/sys/class/power_supply/axp20x-usb")
RUN = os.environ.get("USBLINK_RUN", "/tmp")
MUSB = os.environ.get("WIFI_MUSB", "/sys/devices/platform/soc/1c19000.usb/musb-hdrc.1.auto")
APPS = os.environ.get("USBMODE_APPS", "/mnt/Applications")
CONF = os.path.join(DIR, "usblink.conf")

NETWORKS = ("no", "rndis", "ncm")
NETWORK_NAMES = {"no": "off", "rndis": "Windows", "ncm": "Linux"}
# Windows' DNS server for connection sharing answers "no such name" whenever a
# name has no IPv6 address, and the Nano's C library then gives up on the name
SHARING_DNS = "1.1.1.1"


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def _alive(pidfile: str) -> bool:
    try:
        os.kill(int(_read(pidfile)), 0)
        return True
    except (OSError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# Settings (usblink.conf)
# --------------------------------------------------------------------------- #
def _conf_lines() -> list[str]:
    try:
        with open(CONF, encoding="utf-8", errors="replace") as f:
            return f.read().replace("\r", "").split("\n")
    except OSError:
        return []


def settings() -> dict[str, str]:
    """KEY=value lines as usblink.sh reads them: the last one of a key wins,
    quotes dropped, anything else (comments, blank lines) ignored."""
    out: dict[str, str] = {}
    for line in _conf_lines():
        key, eq, value = line.partition("=")
        if eq and key and key.replace("_", "").isalnum() and key.upper() == key:
            out[key] = value.replace('"', "")
    return out


def set_settings(changes: dict[str, str | None]) -> None:
    """Set (or, with None, remove) keys in usblink.conf, leaving every other
    line, comments included, as it was."""
    lines = _conf_lines()
    while lines and not lines[-1]:
        lines.pop()
    for key, value in changes.items():
        at = [i for i, line in enumerate(lines) if line.startswith(key + "=")]
        new = None if value is None else "%s=%s" % (key, value)
        if at:
            if new is not None:
                lines[at[0]] = new
            for i in reversed(at[1:] if new is not None else at):
                del lines[i]
        elif new is not None:
            lines.append(new)
    os.makedirs(DIR, exist_ok=True)
    tmp = CONF + ".new"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CONF)


def plan() -> dict:
    """What the settings ask for, in the app's terms:
    network   'no', 'rndis' (Windows) or 'ncm' (Linux)
    internet  'off', 'host' (address, route and DNS from the host's DHCP
              server) or 'custom' (a fixed GATEWAY written by hand)
    watch     reconnect by itself after replugs"""
    s = settings()
    network = s.get("NETWORK", "no")
    if network not in ("rndis", "ncm"):
        network = "no"
    if network == "no":
        internet = "off"
    elif s.get("ADDRESS") == "dhcp":
        internet = "host"
    elif s.get("GATEWAY"):
        internet = "custom"
    else:
        internet = "off"
    return {"network": network, "internet": internet, "watch": s.get("WATCH") != "no"}


def set_network(network: str) -> None:
    set_settings({"NETWORK": network if network in NETWORKS else "no"})


def set_internet(on: bool) -> None:
    """Use the host's internet connection (the host has to share it), or not.
    A GATEWAY written by hand ('custom') is never touched: plan() says so and
    the app leaves it alone."""
    s = settings()
    if on:
        changes: dict[str, str | None] = {"ADDRESS": "dhcp"}
        if s.get("NETWORK") == "rndis" and not s.get("DNS"):
            changes["DNS"] = SHARING_DNS
        set_settings(changes)
    elif s.get("ADDRESS") == "dhcp":
        set_settings({"ADDRESS": None})


def set_watch(on: bool) -> None:
    set_settings({"WATCH": "yes" if on else "no"})


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def _link_ip(ifname: str) -> str:
    if not ifname:
        return ""
    try:
        out = subprocess.run(["ip", "-4", "addr", "show", ifname], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True, timeout=5).stdout
    except Exception:
        return ""
    for word, nxt in zip(out.split(), out.split()[1:]):
        if word == "inet":
            return nxt.split("/")[0]
    return ""


def can_host() -> bool:
    """True when the USB controller registered a host root hub (RG Nano).
    GBA Mini and stock FunKey S are peripheral-only."""
    return bool(glob.glob(os.path.join(MUSB, "usb[0-9]*")))


def is_gba_mini() -> bool:
    """True on DrUm78's GBA Mini OS (Start+A/B mapped to X/Y, 240x160 panel)."""
    path = os.environ.get("NANO_FKGPIOD", "/etc/fkgpiod.conf")
    return "START+A" in _read(path).replace(" ", "")


def model() -> str:
    if is_gba_mini():
        return "GBA Mini"
    return "RG Nano" if can_host() else "FunKey S"


def state() -> dict:
    """What the USB port is doing now.
    host       the port is in host mode: the Wi-Fi dongle has it
    mode       'adb' or 'storage' (what the running USB gadget exposes)
    power      USB power is present (a cable to a host or a charger)
    connected  a host has configured the Nano
    link       'rndis' / 'ncm' if the running gadget has a network link, else ''
    ip         the Nano's address on that link, if it has one
    watching   the auto-reconnect watcher is running"""
    host = _read(os.path.join(MUSB, "mode")).startswith("a_")
    mode = "adb" if os.path.isdir(os.path.join(GADGET, "functions", "ffs.adb")) else "storage"
    try:
        udc = sorted(os.listdir(UDCS))[0]
    except (OSError, IndexError):
        udc = ""
    udc_state = _read(os.path.join(UDCS, udc, "state")) if udc else ""
    power = (_read(os.path.join(POWER, "online")) == "1"
             or _read(os.path.join(POWER, "present")) == "1")
    link = ""
    for kind in ("rndis", "ncm"):
        if os.path.exists(os.path.join(GADGET, "configs", "FunKey.1", kind + ".usb0")):
            link = kind
    ip = ""
    if link:
        ip = _link_ip(_read(os.path.join(GADGET, "functions", link + ".usb0", "ifname")))
    return {
        "host": host,
        "mode": mode,
        "power": power,
        "connected": power and udc_state == "configured" and not host,
        "udc_state": udc_state,
        "link": link,
        "ip": ip,
        "watching": _alive(os.path.join(RUN, "usblink-watch.pid")),
    }


def tag(st: dict) -> str:
    """The USB state in a few characters, for a menu row."""
    if st["host"]:
        return "Wi-Fi dongle"
    if st["mode"] == "storage":
        return "storage"
    if not st["connected"]:
        return "ADB, no host"
    return "ADB + net" if st["link"] else "ADB"


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
def reconnect(timeout: int = 90) -> dict:
    """Rebuild the USB connection with usblink.sh (it applies the settings).
    Returns its result line as a dict (connected, fellback, kind, ip, routed,
    dhcp_failed, watch), or {'error': why}."""
    env = dict(os.environ, USBLINK_QUIET="1")
    try:
        p = subprocess.run(["sh", USBLINK_SH], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout, text=True, errors="replace", env=env)
    except Exception as exc:
        return {"error": str(exc)}
    for line in p.stdout.splitlines():
        if line.startswith("result: "):
            kv = dict(w.split("=", 1) for w in line[8:].split() if "=" in w)
            return {
                "connected": kv.get("connected") == "1",
                "fellback": kv.get("fellback") == "1",
                "kind": kv.get("kind", "acm"),
                "ip": "" if kv.get("ip") in (None, "none") else kv["ip"],
                "routed": kv.get("routed") == "1",
                "dhcp_failed": kv.get("dhcp_failed") == "1",
                "watch": kv.get("watch") == "1",
            }
    return {"error": "exit %d" % p.returncode}


def switch_mode() -> int:
    """Switch between ADB and USB storage with usbmode.sh: it flips the marker
    and reboots, so on success this returns just before the Nano goes down.
    Non-zero: the marker could not be written.

    usbmode.sh also swaps its two menu entries ("Switch to USB" / "Switch to
    ADB"). That only makes sense while one of them is in the menu; once they
    are retired (renamed *.opk.off) it would bring one back, so then it is
    pointed at a folder that isn't there and leaves them alone."""
    shown = any(os.path.exists(os.path.join(APPS, n + ".opk")) for n in ("SwitchToUSB", "SwitchToADB"))
    env = dict(os.environ, USBMODE_QUIET="1",
               USBMODE_APPS=APPS if shown else os.path.join(APPS, ".no-switch-entries"))
    try:
        return subprocess.run(["sh", USBMODE_SH], timeout=60, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    except Exception:
        return 1
