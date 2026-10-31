"""
Wi-Fi backend for wifi_gui.py on the stock firmware: drives wifi.sh (port
mode, driver, wpa_supplicant, DHCP) and keeps the saved networks in
wpa_supplicant.conf on the SD card.

Passwords are not stored: a network is saved with the 256-bit key WPA derives
from the password (psk=<64 hex digits>), which is all wpa_supplicant needs.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WIFI_SH = os.environ.get("WIFI_SH", os.path.join(os.path.dirname(HERE), "wifi.sh"))
DATA = os.environ.get("WIFI_DATA", "/mnt/FunKey/wifi")
CONF = os.path.join(DATA, "wpa_supplicant.conf")
RUN = os.environ.get("WIFI_RUN", "/tmp/wifi")

ON_ERRORS = {
    3: ["This firmware is not", "supported (kernel).", "See wifi.log"],
    4: ["Unplug the USB cable,", "plug the Wi-Fi dongle", "in, then try again."],
    5: ["No Wi-Fi dongle found.", "Plug it in with an", "OTG adapter."],
    6: ["wpa_supplicant failed.", "See wifi.log"],
}


def run(args: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout, text=True, errors="replace")
        return p.returncode, p.stdout or ""
    except Exception as exc:
        return 1, str(exc)


def wifi(*args: str, timeout: int = 60) -> tuple[int, str]:
    return run(["sh", WIFI_SH, *args], timeout=timeout)


def fit(value, limit: int = 24) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 2] + ".."


# --------------------------------------------------------------------------- #
# On / off / status
# --------------------------------------------------------------------------- #
def turn_on() -> list[str] | None:
    """Wi-Fi on; None if it is, else the lines that explain why not."""
    rc, _ = wifi("on", timeout=60)
    if rc == 0:
        return None
    return ON_ERRORS.get(rc, ["Wi-Fi did not start", "(error %d)." % rc, "See wifi.log"])


def disconnect() -> None:
    """Wi-Fi off, and the USB port back to the cable."""
    wifi("off", timeout=30)


def status() -> dict:
    _, out = wifi("status", timeout=20)
    kv = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    on = kv.get("wifi") == "on"
    connected = on and kv.get("wpa_state") == "COMPLETED"
    rssi = kv.get("RSSI")
    return {
        "on": on,
        "connected": connected and bool(kv.get("ip")),
        "associated": connected,
        "ssid": decode_ssid(kv.get("ssid", "")).decode("utf-8", "replace") if connected else "",
        "ip": kv.get("ip", ""),
        "signal": (rssi + " dBm") if rssi and rssi.lstrip("-").isdigit() else "",
    }


# --------------------------------------------------------------------------- #
# Scan
# --------------------------------------------------------------------------- #
def decode_ssid(s: str) -> bytes:
    """wpa_cli prints SSIDs escaped (\\xNN, \\\\, \\", \\n ...): back to bytes."""
    out = bytearray()
    i = 0
    esc = {"n": b"\n", "r": b"\r", "t": b"\t", "e": b"\x1b", "\\": b"\\", '"': b'"'}
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n == "x" and re.fullmatch(r"[0-9a-fA-F]{2}", s[i + 2:i + 4] or ""):
                out.append(int(s[i + 2:i + 4], 16))
                i += 4
                continue
            out += esc.get(n, n.encode())
            i += 2
            continue
        out += c.encode("utf-8")
        i += 1
    return bytes(out)


def security(flags: str) -> str:
    """'open', 'psk' (WPA/WPA2 with a password) or 'other' (WPA3-only, WEP,
    enterprise: this wpa_supplicant can't join those)."""
    if "PSK" in flags:
        return "psk"
    if any(k in flags for k in ("WPA", "RSN", "WEP", "SAE", "EAP")):
        return "other"
    return "open"


def scan() -> list[dict]:
    if turn_on():
        return []
    _, out = wifi("scan", timeout=40)
    best: dict[bytes, dict] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or not parts[0].lstrip("-").isdigit():
            continue
        raw = decode_ssid(parts[2])
        if not raw.strip(b"\0"):
            continue            # hidden network
        net = {"raw": raw, "ssid": raw.decode("utf-8", "replace"), "signal": int(parts[0]),
               "sec": security(parts[1])}
        net["enc"] = net["sec"] != "open"
        if raw not in best or net["signal"] > best[raw]["signal"]:
            best[raw] = net
    return sorted(best.values(), key=lambda n: n["signal"], reverse=True)


def signal_bars(dbm: int | None) -> str:
    if dbm is None:
        return "    "
    if dbm >= -55:
        return "||||"
    if dbm >= -67:
        return "||| "
    if dbm >= -75:
        return "||  "
    return "|   "


# --------------------------------------------------------------------------- #
# Saved networks (wpa_supplicant.conf)
# --------------------------------------------------------------------------- #
def _conf_ssid(value: str) -> bytes:
    value = value.strip()
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return value[1:-1].encode("utf-8")
    try:
        return bytes.fromhex(value)
    except ValueError:
        return value.encode("utf-8")


def read_conf() -> tuple[list[str], list[list[str]]]:
    """(header lines, network blocks as lists of 'key=value' lines)."""
    header: list[str] = []
    blocks: list[list[str]] = []
    cur: list[str] | None = None
    try:
        with open(CONF, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    for line in lines:
        s = line.strip()
        if cur is None:
            if s.startswith("network={"):
                cur = []
            elif s:
                header.append(s)
        elif s == "}":
            blocks.append(cur)
            cur = None
        elif s:
            cur.append(s)
    if not any(h.startswith("ctrl_interface=") for h in header):
        header.insert(0, "ctrl_interface=" + os.path.join(RUN, "ctrl"))
    if "update_config=1" not in header:
        header.append("update_config=1")
    return header, blocks


def write_conf(header: list[str], blocks: list[list[str]]) -> None:
    os.makedirs(DATA, exist_ok=True)
    text = "\n".join(header) + "\n"
    for b in blocks:
        text += "\nnetwork={\n" + "".join("\t%s\n" % kv for kv in b) + "}\n"
    tmp = CONF + ".new"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CONF)


def block_ssid(block: list[str]) -> bytes | None:
    for kv in block:
        if kv.startswith("ssid="):
            return _conf_ssid(kv[5:])
    return None


def psk_for(ssid: bytes, password: str) -> str:
    """The WPA key for a password: 64 hex digits as they are, else PBKDF2."""
    if re.fullmatch(r"[0-9a-fA-F]{64}", password):
        return password.lower()
    return hashlib.pbkdf2_hmac("sha1", password.encode("utf-8"), ssid, 4096, 32).hex()


def save_network(ssid: bytes, password: str) -> list[list[str]]:
    """Save (or replace) a network, ahead of the others; returns the old blocks."""
    header, blocks = read_conf()
    old = [b for b in blocks if block_ssid(b) != ssid]
    top = max([int(kv.split("=", 1)[1]) for b in old for kv in b
               if kv.startswith("priority=") and kv.split("=", 1)[1].isdigit()] or [0])
    new = ["ssid=" + ssid.hex()]
    if password:
        new.append("psk=" + psk_for(ssid, password))
    else:
        new.append("key_mgmt=NONE")
    new.append("priority=%d" % (top + 1))
    write_conf(header, [new] + old)
    return old


def forget_network(ssid: bytes) -> None:
    header, blocks = read_conf()
    write_conf(header, [b for b in blocks if block_ssid(b) != ssid])


def connect(ssid, password: str, timeout: int = 40) -> bool:
    """Join a network and wait for an address. A network that won't join is
    not kept (a wrong password would only make it retry forever)."""
    raw = ssid if isinstance(ssid, bytes) else str(ssid).encode("utf-8")
    if turn_on():
        return False
    save_network(raw, password)
    wifi("cli", "reconfigure", timeout=15)
    wifi("cli", "reassociate", timeout=15)
    want = raw.decode("utf-8", "replace")
    end = time.time() + timeout
    joined = False
    while time.time() < end:
        st = status()
        if st["associated"] and st["ssid"] == want:
            joined = True
            break
        time.sleep(1)
    if not joined:
        forget_network(raw)
        wifi("cli", "reconfigure", timeout=15)
        return False
    # ask for an address now instead of at the next DHCP retry
    try:
        with open(os.path.join(RUN, "udhcpc.pid")) as f:
            os.kill(int(f.read().strip()), 10)      # SIGUSR1: renew
    except (OSError, ValueError):
        pass
    end = time.time() + 20
    while time.time() < end:
        if status()["connected"]:
            return True
        time.sleep(1)
    return status()["connected"]


# --------------------------------------------------------------------------- #
# Clock
# --------------------------------------------------------------------------- #
def local_now() -> str:
    return time.strftime("%a %d %b  %H:%M")


def _setting(key: str) -> str:
    try:
        with open(os.path.join(DATA, "wifi.conf"), encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def sync_clock() -> str:
    """Set the clock from the internet. The time comes from a web server's
    Date header over plain HTTP (HTTPS can't be checked with a wrong clock).
    Like the firmware, the clock holds local time: TZ in wifi.conf says which,
    and without it the clock is left alone."""
    import email.utils
    import http.client

    tz = _setting("TZ")
    if not tz:
        return "Set TZ in wifi.conf"
    utc = None
    for host in ("www.google.com", "www.cloudflare.com", "nrk.no"):
        try:
            c = http.client.HTTPConnection(host, 80, timeout=8)
            c.request("HEAD", "/")
            date = c.getresponse().getheader("Date")
            c.close()
            if date:
                utc = email.utils.parsedate_to_datetime(date).timestamp()
                break
        except Exception:
            continue
    if utc is None:
        return "No time server reachable"
    # UTC -> local by the system's own date: the bundled Python has no
    # time.tzset() (configure can't test it when cross-compiling)
    try:
        stamp = subprocess.run(["date", "-d", "@%d" % int(utc), "+%Y-%m-%d %H:%M:%S"],
                               stdout=subprocess.PIPE, text=True, timeout=10,
                               env=dict(os.environ, TZ=tz)).stdout.strip()
    except Exception:
        stamp = ""
    if not stamp:
        return "Time conversion failed"
    rc, out = run(["date", "-s", stamp], timeout=10)
    if rc != 0:
        return "date failed: " + out.strip()[:20]
    run(["hwclock", "-w"], timeout=10)
    return "Set to " + stamp[11:16]
