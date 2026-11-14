"""UTC clock normalization for FunKey-OS (kernel clock holds local wall time).

FunKey-OS does not maintain /etc/TZ system-wide and stores local wall-clock
time in both the Linux kernel clock and the PCF8563 RTC. Cross-compiled
Python 3.12 on the Nano also lacks time.tzset().

This module evaluates POSIX TZ strings directly in pure Python so both the
Connect sync UI and RetroAchievements queue flusher can convert kernel wall
time to accurate UTC seconds and persist /mnt/FunKey/raoffline/clock_sync.json
for picoarch (C).
"""
from __future__ import annotations

import calendar
from datetime import datetime, timezone
import json
import os
import re
import time

RA_DATA = os.environ.get("RA_DATA", "/mnt/FunKey/raoffline")
WIFI_CONF = os.environ.get("WIFI_CONF", "/mnt/FunKey/wifi/wifi.conf")

_TZ_RE = re.compile(
    r"^(?P<std><[^>]+>|[A-Za-z]+)"
    r"(?P<std_off>[+-]?\d+(?::\d{1,2}(?::\d{1,2})?)?)"
    r"(?:(?P<dst><[^>]+>|[A-Za-z]+)"
    r"(?P<dst_off>[+-]?\d+(?::\d{1,2}(?::\d{1,2})?)?)?"
    r"(?:,(?P<start>[^,]+),(?P<end>[^,]+))?)?$"
)


def _ra_dir() -> str:
    return os.environ.get("RA_DATA", RA_DATA)


def clock_sync_path() -> str:
    return os.path.join(_ra_dir(), "clock_sync.json")


def _parse_hms_west(spec: str) -> int:
    """Parse POSIX offset west of UTC ([+-]HH[:MM[:SS]]) -> signed seconds east of UTC."""
    sign = 1
    if spec[0] in "+-":
        sign = -1 if spec[0] == "-" else 1
        spec = spec[1:]
    parts = [int(p) for p in spec.split(":")]
    h = parts[0]
    m = parts[1] if len(parts) > 1 else 0
    s = parts[2] if len(parts) > 2 else 0
    west_sec = sign * (h * 3600 + m * 60 + s)
    return -west_sec


def _parse_rule_time(spec: str | None) -> int:
    if not spec:
        return 2 * 3600
    sign = -1 if spec[0] == "-" else 1
    raw = spec[1:] if spec[0] in "+-" else spec
    parts = [int(p) for p in raw.split(":")]
    h = parts[0]
    m = parts[1] if len(parts) > 1 else 0
    s = parts[2] if len(parts) > 2 else 0
    return sign * (h * 3600 + m * 60 + s)


def _nth_weekday_epoch(year: int, month: int, week: int, wday: int) -> int:
    """UTC midnight epoch of week-th weekday in month (wday: 0=Sun..6=Sat)."""
    first_mon_wday, days_in_month = calendar.monthrange(year, month)
    # calendar.monthrange returns 0=Mon..6=Sun; convert to 0=Sun..6=Sat
    first_sun_wday = (first_mon_wday + 1) % 7
    day = 1 + (wday - first_sun_wday) % 7 + (week - 1) * 7
    while day > days_in_month:
        day -= 7
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp())


def _rule_local_epoch(year: int, spec: str) -> int:
    if "/" in spec:
        date_part, time_part = spec.split("/", 1)
    else:
        date_part, time_part = spec, None
    time_sec = _parse_rule_time(time_part)

    if date_part.startswith(("M", "m")):
        m_str, w_str, d_str = date_part[1:].split(".")
        base = _nth_weekday_epoch(year, int(m_str), int(w_str), int(d_str))
        return base + time_sec

    jan1 = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())
    if date_part.startswith("J"):
        n = int(date_part[1:])  # 1..365, Feb 29 skipped
        is_leap = calendar.isleap(year)
        day_idx = n - 1 + (1 if is_leap and n > 59 else 0)
        return jan1 + day_idx * 86400 + time_sec

    day_idx = int(date_part)  # 0..365, Feb 29 counted
    return jan1 + day_idx * 86400 + time_sec


def parse_posix_tz(tz: str) -> dict | None:
    tz = (tz or "").strip()
    if not tz:
        return None
    m = _TZ_RE.match(tz)
    if not m:
        return None
    std_east = _parse_hms_west(m.group("std_off"))
    dst = m.group("dst")
    dst_off = m.group("dst_off")
    dst_east = _parse_hms_west(dst_off) if dst_off else (std_east + 3600 if dst else std_east)
    return {
        "tz": tz,
        "std_east": std_east,
        "dst_east": dst_east,
        "has_dst": bool(dst),
        "start": m.group("start"),
        "end": m.group("end"),
    }


def _dst_bounds_utc(parsed: dict, year: int) -> tuple[int, int] | None:
    if not parsed["has_dst"] or not parsed["start"] or not parsed["end"]:
        return None
    start_local = _rule_local_epoch(year, parsed["start"])
    end_local = _rule_local_epoch(year, parsed["end"])
    start_utc = start_local - parsed["std_east"]
    end_utc = end_local - parsed["dst_east"]
    return start_utc, end_utc


def utc_offset_for_utc(tz: str, utc_sec: int) -> int | None:
    parsed = parse_posix_tz(tz)
    if not parsed:
        return None
    if not parsed["has_dst"]:
        return parsed["std_east"]
    year = datetime.fromtimestamp(utc_sec, tz=timezone.utc).year
    bounds = _dst_bounds_utc(parsed, year)
    if bounds is None:
        return parsed["std_east"]
    start_utc, end_utc = bounds
    if start_utc <= end_utc:
        in_dst = start_utc <= utc_sec < end_utc
    else:
        # Southern hemisphere DST wraps across New Year
        in_dst = utc_sec >= start_utc or utc_sec < end_utc
    return parsed["dst_east"] if in_dst else parsed["std_east"]


def local_epoch_to_utc(tz: str, local_epoch: int) -> int | None:
    parsed = parse_posix_tz(tz)
    if not parsed:
        return None
    if not parsed["has_dst"]:
        return local_epoch - parsed["std_east"]
    cand = local_epoch - parsed["std_east"]
    off = utc_offset_for_utc(tz, cand)
    if off == parsed["dst_east"]:
        return local_epoch - parsed["dst_east"]
    cand_dst = local_epoch - parsed["dst_east"]
    off_dst = utc_offset_for_utc(tz, cand_dst)
    if off_dst == parsed["dst_east"]:
        return cand_dst
    return cand


def read_configured_tz() -> str:
    conf_path = os.environ.get("WIFI_CONF", WIFI_CONF)
    try:
        with open(conf_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if s.startswith("TZ="):
                    return s.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    sync = read_clock_sync()
    return str(sync.get("posix_tz") or "")


def read_clock_sync() -> dict:
    try:
        with open(clock_sync_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_clock_sync(utc_sec: int, posix_tz: str = "") -> dict:
    tz = (posix_tz or read_configured_tz()).strip()
    off = utc_offset_for_utc(tz, utc_sec) if tz else None
    if off is None:
        off = int(round(time.time() - utc_sec))
    payload = {
        "posix_tz": tz,
        "utc_offset_sec": int(off),
        "last_sync_utc": int(utc_sec),
    }
    path = clock_sync_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return payload


def now_utc(kernel_now: int | None = None) -> int:
    """Return current UTC epoch seconds on FunKey-OS (or standard host if no TZ/sync)."""
    raw = int(time.time() if kernel_now is None else kernel_now)
    tz = read_configured_tz()
    if tz:
        converted = local_epoch_to_utc(tz, raw)
        if converted is not None:
            return converted
    sync = read_clock_sync()
    if "utc_offset_sec" in sync:
        return raw - int(sync["utc_offset_sec"])
    return raw
