#!/usr/bin/env python3
"""Small terminal YouTube app for the RG Nano.

The RG Nano build does not have the RG34XXSP app's framebuffer/Pillow/evdev
stack. This app keeps the same YouTube extraction idea but uses st-sdl for a
simple terminal UI and MPlayer for playback.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    import select
    import termios
    import tty
except ImportError:  # local preview on Windows
    select = None
    termios = None
    tty = None


PREVIEW_ARG = os.environ.get("NANOPLAY_PREVIEW") == "1" or "preview" in sys.argv or "--preview" in sys.argv
DEFAULT_APP_DIR: str | Path = Path(__file__).resolve().parent if PREVIEW_ARG else "/mnt/FunKey/youtube"
APP_DIR = Path(os.environ.get("YOUTUBE_APP_DIR", DEFAULT_APP_DIR))
DATA_DIR = APP_DIR / "data"
CACHE_DIR = APP_DIR / "cache"
WORK_DIR = Path("/tmp/youtube_nano")
# Hand-off file between the two launch phases: the menu (in `st`) writes the
# chosen video here and exits; run.sh then runs the standalone player (no `st`).
PLAY_REQUEST = Path("/tmp/yt_play.json")
LOG_FILE = APP_DIR / "youtube-debug.log"

YTDLP = APP_DIR / "yt-dlp"
COOKIES = APP_DIR / "cookies.txt"
# The custom firmware had qjs in /usr/bin; on stock firmware the OPK brings it
QJS = os.environ.get("NANOPLAY_QJS", "/usr/bin/qjs")
MPLAYER_OPK = "/media/Applications/MPlayer.opk"
MPLAYER_DIR = Path("/tmp/mp")
APP_NAME = "NanoPlay"
APP_TAGLINE = "small screen. real video."
SCREEN_WIDTH = int(os.environ.get("NANOPLAY_SCREEN_WIDTH", "38"))
LIST_PAGE_SIZE = int(os.environ.get("NANOPLAY_LIST_ROWS", "9"))
# Blank lines above every screen: the GBA Mini's 240x160 panel shows only the
# middle rows of the 240-row terminal (run.sh sets this there)
TOP_PAD = int(os.environ.get("NANOPLAY_TOP_PAD", "0"))
SEARCH_CACHE_TTL = 60 * 60 * 24
META_CACHE_TTL = 60 * 60 * 24 * 7
PLAYER_LABEL = "360p -> 240x135"
PLAYBACK_MODES = ("auto", "cache", "stream")
LONG_VIDEO_STREAM_SECONDS = 30 * 60
USE_COLOR = os.environ.get("NO_COLOR") is None
PREVIEW_MODE = PREVIEW_ARG

# 360p single combined stream (itag 18 = H.264 + AAC). Measured on device to
# decode in realtime with ~3x headroom, and being ONE stream it plays via a
# single FIFO -> MPlayer (the proven NRK path) -- no -audiofile muxing. There is
# no combined stream below 360p on modern YouTube, so this is the lowest single
# stream available; the screen is 240px wide so it is downscaled anyway.
FORMAT_PLAYBACK = "best[height<=360][vcodec!=none][acodec!=none]/18"
FORMAT_SORT = "vcodec:h264,res:360"
MAX_HEIGHT = 360
# Try the android client first: it returns itag 18 with a direct URL (no qjs
# signature work) so extraction is ~2 min faster than web. Fall back to web.
PLAYER_CLIENTS = "android,web"
FAST_YOUTUBE_CLIENT = {
    "clientName": "ANDROID",
    "clientVersion": "21.02.35",
    "androidSdkVersion": 30,
    "userAgent": "com.google.android.youtube/21.02.35 (Linux; U; Android 11) gzip",
    "osName": "Android",
    "osVersion": "11",
    "hl": "en",
}

# Known-good videos for quickly re-testing the app without searching. These are
# long-standing, globally available, non-age-restricted uploads that expose the
# standard itag 18 (360p H.264 + AAC) combined stream the player needs.
TEST_VIDEOS = [
    {
        "title": "lofi hip hop radio",
        "id": "n61ULEU7CO0",
        "url": "https://www.youtube.com/watch?v=n61ULEU7CO0",
        "channel": "Lofi Girl",
        "known_good": True,
    },
    {
        "title": "Me at the zoo (first YouTube video, 19s)",
        "id": "jNQXAC9IVRw",
        "url": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        "channel": "jawed",
        "duration": 19,
        "known_good": True,
    },
    {
        "title": "Big Buck Bunny (Blender open movie)",
        "id": "YE7VzlLtp-4",
        "url": "https://www.youtube.com/watch?v=YE7VzlLtp-4",
        "channel": "Blender",
        "known_good": True,
    },
    {
        "title": "Rick Astley - Never Gonna Give You Up",
        "id": "dQw4w9WgXcQ",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "channel": "Rick Astley",
        "known_good": True,
    },
]

CATEGORIES = [
    ("Tiny Picks", "retro handheld gameplay no commentary"),
    ("Quick Watch", "short interesting videos under 10 minutes"),
    ("Lofi", "lofi hip hop radio chill beats"),
    ("Synthwave", "synthwave live set visualizer"),
    ("Retro", "retro gaming handheld review"),
    ("Tech", "technology review handheld"),
    ("Gaming", "gameplay no commentary"),
    ("Documentary", "short documentary"),
    ("Cooking", "quick cooking recipe tutorial"),
    ("Comedy", "funny videos comedy"),
]

PINNED_SEARCHES = [
    "retro handhelds",
    "lofi hip hop",
    "synthwave",
    "tech reviews",
    "gameplay no commentary",
]

SEARCH_KEY_ROWS: list[list[str]] = [
    list("abcdefg"),
    list("hijklmn"),
    list("opqrstu"),
    list("vwxyz09"),
    list("12345678"),
    ["SP", "DEL", "OK"],
]

URL_KEY_ROWS: list[list[str]] = [
    list("abcdefg"),
    list("hijklmn"),
    list("opqrstu"),
    list("vwxyz09"),
    list("1234567"),
    list("8.-_:/?"),
    list("=&#%"),
    ["YT", "CASE", "DEL", "OK"],
]


def now() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


def safe_title(value: Any, limit: int = 70) -> str:
    text = str(value or "Unknown").replace("\n", " ").strip()
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def fit(value: Any, limit: int = SCREEN_WIDTH) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def color(text: str, code: str) -> str:
    if not USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def accent(text: str) -> str:
    return color(text, "31;1")


def dim(text: str) -> str:
    # st-sdl on the RG Nano logs SGR 2 ("dim") as an unknown attribute.
    return text


def ok_text(text: str) -> str:
    return color(text, "32;1")


def warn_text(text: str) -> str:
    return color(text, "33;1")


def divider(char: str = "=") -> str:
    return char * SCREEN_WIDTH


def format_duration(value: Any) -> str:
    total = parse_duration_seconds(value)
    if total <= 0:
        return ""
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def parse_duration_seconds(value: Any) -> int:
    try:
        total = int(value or 0)
    except (TypeError, ValueError):
        total = 0
    return max(0, total)


def format_elapsed(start: float) -> str:
    elapsed = max(0, int(time.time() - start))
    minutes, seconds = divmod(elapsed, 60)
    return f"{minutes:02d}:{seconds:02d}"


def format_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    size = float(max(0, value))
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024


def cache_key(value: str) -> str:
    import hashlib

    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()


def video_url(video: dict[str, Any]) -> str:
    url = str(video.get("url") or "")
    if url:
        return url
    vid = str(video.get("id") or "")
    if vid:
        return f"https://www.youtube.com/watch?v={vid}"
    return ""


def youtube_video_id(url: str) -> str:
    import urllib.parse

    text = str(url or "").strip()
    if re.fullmatch(r"[0-9A-Za-z_-]{11}", text):
        return text
    try:
        parsed = urllib.parse.urlparse(text)
    except Exception:
        return ""
    host = parsed.netloc.lower()
    if host.endswith("youtu.be"):
        candidate = parsed.path.strip("/").split("/", 1)[0]
        return candidate if re.fullmatch(r"[0-9A-Za-z_-]{11}", candidate or "") else ""
    if "youtube.com" in host:
        query_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
        if re.fullmatch(r"[0-9A-Za-z_-]{11}", query_id or ""):
            return query_id
        parts = [part for part in parsed.path.split("/") if part]
        for prefix in ("shorts", "embed", "live"):
            if prefix in parts:
                idx = parts.index(prefix)
                if idx + 1 < len(parts) and re.fullmatch(r"[0-9A-Za-z_-]{11}", parts[idx + 1]):
                    return parts[idx + 1]
    match = re.search(r"(?<![0-9A-Za-z_-])([0-9A-Za-z_-]{11})(?![0-9A-Za-z_-])", text)
    return match.group(1) if match else ""


def clear() -> None:
    if PREVIEW_MODE:
        sys.stdout.write("\n" + "-" * SCREEN_WIDTH + "\n")
        return
    # ANSI home + clear. Avoids spawning `clear` (a fork/exec per redraw is
    # costly on the single A7) and works even if `st` terminfo is absent.
    sys.stdout.write("\033[H\033[2J" + "\n" * TOP_PAD)
    sys.stdout.flush()


def ensure_clock() -> None:
    """HTTPS needs a roughly right clock, and the GBA Mini forgets the time at
    power-off (1970 after a cold start). If the clock is clearly wrong, set it
    from a web server's Date header over plain HTTP, in local time like the
    firmware keeps it (NANOPLAY_TZ, default Norway's)."""
    if time.time() > 1704067200:  # after 2024-01-01: leave it alone
        return
    import email.utils
    import http.client

    utc = None
    for host in ("www.google.com", "nrk.no"):
        try:
            conn = http.client.HTTPConnection(host, 80, timeout=6)
            conn.request("HEAD", "/")
            date = conn.getresponse().getheader("Date")
            conn.close()
            if date:
                utc = email.utils.parsedate_to_datetime(date).timestamp()
                break
        except Exception:
            continue
    if utc is None:
        log("clock: wrong and no time server reachable")
        return
    # UTC -> local by the system's own date: this Python has no time.tzset()
    # (configure can't test it when cross-compiling, so it is left out)
    tz = os.environ.get("NANOPLAY_TZ", "CET-1CEST,M3.5.0,M10.5.0/3")
    stamp = subprocess.run(["date", "-d", "@%d" % int(utc), "+%Y-%m-%d %H:%M:%S"],
                           stdout=subprocess.PIPE, text=True, env=dict(os.environ, TZ=tz)).stdout.strip()
    if not stamp:
        log("clock: could not convert the time")
        return
    subprocess.run(["date", "-s", stamp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log(f"clock: was before 2024, set to {stamp}")


def pause(message: str = "Press Enter...") -> None:
    if PREVIEW_MODE:
        return
    if termios is not None and sys.stdin.isatty():
        print("")
        print(dim("A/B to continue"))
        with RawInput():
            read_button()
        return
    try:
        input(f"\n{message}")
    except EOFError:
        pass


class RawInput:
    def __init__(self) -> None:
        self.fd: int | None = None
        self.old: Any = None

    def __enter__(self) -> "RawInput":
        if termios is None or tty is None or not sys.stdin.isatty():
            return self
        self.fd = sys.stdin.fileno()
        self.old = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        return self

    def __exit__(self, *_args: Any) -> None:
        if termios is not None and self.fd is not None and self.old is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)


def read_button() -> str:
    ch = sys.stdin.read(1)
    if ch == "\x1b" and select is not None:
        seq = ch
        while True:
            ready, _, _ = select.select([sys.stdin], [], [], 0.02)
            if not ready:
                break
            seq += sys.stdin.read(1)
            if len(seq) >= 3:
                break
        if seq == "\x1b[A":
            return "up"
        if seq == "\x1b[B":
            return "down"
        if seq == "\x1b[C":
            return "right"
        if seq == "\x1b[D":
            return "left"
        return "back"

    mapping = {
        "\r": "select",
        "\n": "select",
        " ": "select",
        "a": "select",
        "A": "select",
        "b": "back",
        "B": "back",
        "q": "quit",
        "Q": "quit",
        "m": "quit",
        "M": "quit",
        "u": "up",
        "U": "up",
        "k": "up",
        "K": "up",
        "d": "down",
        "D": "down",
        "j": "down",
        "J": "down",
        "l": "left",
        "L": "left",
        "h": "left",
        "H": "left",
        "r": "right",
        "R": "right",
        "f": "favorite",
        "F": "favorite",
        "y": "favorite",
        "Y": "favorite",
        "w": "watch",
        "W": "watch",
        "i": "info",
        "I": "info",
        "x": "info",
        "X": "info",
        "s": "search",
        "S": "search",
    }
    return mapping.get(ch, ch)


def read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"read_json failed for {path}: {exc}")
    return default


def write_json(path: Path, value: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def log(message: str) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(f"[{now()}] {message}\n")
    except Exception:
        pass


class Store:
    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.history_path = DATA_DIR / "history.json"
        self.favorites_path = DATA_DIR / "favorites.json"
        self.watch_later_path = DATA_DIR / "watch_later.json"
        self.search_path = DATA_DIR / "search_history.json"
        self.settings_path = DATA_DIR / "settings.json"
        self.search_cache_path = DATA_DIR / "search_cache.json"
        self.metadata_cache_path = DATA_DIR / "metadata_cache.json"
        self.failures_path = DATA_DIR / "failures.json"
        self.known_good_path = DATA_DIR / "known_good.json"
        self.history: list[dict[str, Any]] = read_json(self.history_path, [])
        self.favorites: list[dict[str, Any]] = read_json(self.favorites_path, [])
        self.watch_later: list[dict[str, Any]] = read_json(self.watch_later_path, [])
        self.search_history: list[str] = read_json(self.search_path, [])
        self.settings: dict[str, Any] = read_json(
            self.settings_path,
            {
                "results": 10,
                "format": FORMAT_PLAYBACK,
                "playback_mode": "auto",
                "pinned_searches": PINNED_SEARCHES,
                "color": True,
            },
        )
        self.search_cache: dict[str, Any] = read_json(self.search_cache_path, {})
        self.metadata_cache: dict[str, Any] = read_json(self.metadata_cache_path, {})
        self.failures: dict[str, Any] = read_json(self.failures_path, {})
        self.known_good: dict[str, Any] = read_json(self.known_good_path, {})

    def save(self) -> None:
        write_json(self.history_path, self.history[:50])
        write_json(self.favorites_path, self.favorites[:100])
        write_json(self.watch_later_path, self.watch_later[:100])
        write_json(self.search_path, self.search_history[:20])
        write_json(self.settings_path, self.settings)
        write_json(self.search_cache_path, self.search_cache)
        write_json(self.metadata_cache_path, self.metadata_cache)
        write_json(self.failures_path, self.failures)
        write_json(self.known_good_path, self.known_good)

    def add_search(self, query: str) -> None:
        query = query.strip()
        if not query:
            return
        self.search_history = [q for q in self.search_history if q != query]
        self.search_history.insert(0, query)
        self.save()

    def add_history(self, video: dict[str, Any]) -> None:
        url = video_url(video)
        self.history = [item for item in self.history if item.get("url") != url]
        entry = dict(video)
        entry["url"] = url
        entry["watched"] = now()
        self.history.insert(0, entry)
        self.save()

    def is_favorite(self, video: dict[str, Any]) -> bool:
        url = video_url(video)
        return any(item.get("url") == url for item in self.favorites)

    def toggle_favorite(self, video: dict[str, Any]) -> bool:
        url = video_url(video)
        if not url:
            return False
        if self.is_favorite(video):
            self.favorites = [item for item in self.favorites if item.get("url") != url]
            self.save()
            return False
        entry = dict(video)
        entry["url"] = url
        entry["favorited"] = now()
        self.favorites.insert(0, entry)
        self.save()
        return True

    def is_watch_later(self, video: dict[str, Any]) -> bool:
        url = video_url(video)
        return any(item.get("url") == url for item in self.watch_later)

    def toggle_watch_later(self, video: dict[str, Any]) -> bool:
        url = video_url(video)
        if not url:
            return False
        if self.is_watch_later(video):
            self.watch_later = [item for item in self.watch_later if item.get("url") != url]
            self.save()
            return False
        entry = dict(video)
        entry["url"] = url
        entry["saved"] = now()
        self.watch_later.insert(0, entry)
        self.save()
        return True

    def pinned_searches(self) -> list[str]:
        pins = self.settings.get("pinned_searches", PINNED_SEARCHES)
        return [str(item) for item in pins if str(item).strip()][:8]

    def search_cache_id(self, query: str, count: int) -> str:
        return cache_key(f"{query.strip().lower()}|{count}")

    def get_search_cache(self, query: str, count: int) -> list[dict[str, Any]] | None:
        entry = self.search_cache.get(self.search_cache_id(query, count))
        if not isinstance(entry, dict):
            return None
        try:
            age = time.time() - float(entry.get("saved_at", 0))
        except (TypeError, ValueError):
            return None
        videos = entry.get("videos")
        if age <= SEARCH_CACHE_TTL and isinstance(videos, list):
            return videos
        return None

    def set_search_cache(self, query: str, count: int, videos: list[dict[str, Any]]) -> None:
        self.search_cache[self.search_cache_id(query, count)] = {
            "query": query,
            "count": count,
            "saved_at": time.time(),
            "videos": videos[:25],
        }
        self.save()

    def metadata_key(self, url: str) -> str:
        return cache_key(url)

    def get_metadata(self, url: str) -> dict[str, Any] | None:
        entry = self.metadata_cache.get(self.metadata_key(url))
        if not isinstance(entry, dict):
            return None
        try:
            age = time.time() - float(entry.get("saved_at", 0))
        except (TypeError, ValueError):
            return None
        if age <= META_CACHE_TTL:
            return entry
        return None

    def set_metadata(self, url: str, info: dict[str, Any]) -> None:
        self.metadata_cache[self.metadata_key(url)] = {
            "saved_at": time.time(),
            "title": info.get("title"),
            "channel": info.get("channel") or info.get("uploader"),
            "height": info.get("height"),
            "format_id": info.get("format_id"),
            "duration": info.get("duration"),
        }
        self.save()

    def failure_for(self, video: dict[str, Any] | str) -> dict[str, Any] | None:
        url = video if isinstance(video, str) else video_url(video)
        entry = self.failures.get(cache_key(str(url)))
        return entry if isinstance(entry, dict) else None

    def mark_failed(self, video: dict[str, Any] | str, reason: str) -> None:
        url = video if isinstance(video, str) else video_url(video)
        if not url:
            return
        self.failures[cache_key(str(url))] = {
            "url": str(url),
            "reason": reason,
            "when": now(),
        }
        self.save()

    def mark_success(self, video: dict[str, Any] | str) -> None:
        url = video if isinstance(video, str) else video_url(video)
        if not url:
            return
        key = cache_key(str(url))
        self.known_good[key] = {"url": str(url), "when": now()}
        if key in self.failures:
            del self.failures[key]
        self.save()

    def is_known_good(self, video: dict[str, Any]) -> bool:
        if video.get("known_good"):
            return True
        return cache_key(video_url(video)) in self.known_good

    def clear_history(self) -> None:
        self.history = []
        self.save()

    def clear_cache_files(self) -> None:
        import shutil

        self.search_cache = {}
        self.metadata_cache = {}
        if CACHE_DIR.exists():
            shutil.rmtree(CACHE_DIR, ignore_errors=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.save()

    def clear_failures(self) -> None:
        self.failures = {}
        self.save()


class YouTubeApp:
    def __init__(self) -> None:
        self.store = Store()
        self.last_results: list[dict[str, Any]] = []
        global USE_COLOR
        USE_COLOR = os.environ.get("NO_COLOR") is None and bool(self.store.settings.get("color", True))

    def status_tokens(self) -> str:
        cookies = "CK" if COOKIES.exists() else "NOCK"
        try:
            cache_warm = CACHE_DIR.exists() and any(CACHE_DIR.iterdir())
        except OSError:
            cache_warm = False
        cache_text = "WARM" if cache_warm else "COLD"
        return f"{cookies} {cache_text}"

    def playback_mode(self) -> str:
        mode = str(self.store.settings.get("playback_mode", "auto")).lower()
        if mode not in PLAYBACK_MODES:
            mode = "auto"
            self.store.settings["playback_mode"] = mode
            self.store.save()
        return mode

    def playback_mode_label(self) -> str:
        labels = {
            "auto": "auto",
            "cache": "cache",
            "stream": "stream",
        }
        return labels.get(self.playback_mode(), "auto")

    def header(self, title: str = APP_NAME, status: str = "") -> None:
        clear()
        inner = SCREEN_WIDTH - 2
        left = fit(title, 15)
        right = fit(status, inner - len(left) - 1) if status else ""
        print(accent("+" + "-" * inner + "+"))
        if right:
            pad = max(1, inner - len(left) - len(right))
            print("|" + accent(left) + (" " * pad) + dim(right) + "|")
        else:
            print("|" + accent(left) + (" " * max(0, inner - len(left))) + "|")
        print(accent("+" + "-" * inner + "+"))

    def footer(self, text: str) -> None:
        if not text:
            return
        print("")
        print(dim("[" + fit(text, SCREEN_WIDTH - 2).center(SCREEN_WIDTH - 2) + "]"))

    def menu_item(self, key: str, label: str, detail: str = "") -> None:
        text = f"{key} {label}"
        if detail:
            free = SCREEN_WIDTH - len(text) - 1
            if free > 4:
                text = text + " " + fit(detail, free)
        print(fit(text, SCREEN_WIDTH))

    def pixel_row(self, label: str, detail: str = "", selected: bool = False) -> None:
        inner = SCREEN_WIDTH - 4
        marker = ">" if selected else " "
        text = fit(label, inner)
        if detail:
            free = inner - len(text) - 1
            if free > 3:
                text = text + " " + fit(detail, free)
        row = f"{marker} {text.ljust(inner)}"
        if selected:
            row = color(row, "7;31")
        print(row)

    def select_menu(
        self,
        title: str,
        items: list[dict[str, Any]],
        footer: str = "U/D move  A ok  B back",
        status: str = "",
        start: int = 0,
    ) -> dict[str, Any] | None:
        if not items:
            self.header(title, status or self.status_tokens())
            print("Nothing here yet.")
            pause()
            return None
        selected = max(0, min(start, len(items) - 1))
        offset = 0
        with RawInput():
            while True:
                if selected < offset:
                    offset = selected
                if selected >= offset + LIST_PAGE_SIZE:
                    offset = selected - LIST_PAGE_SIZE + 1
                visible = items[offset : offset + LIST_PAGE_SIZE]
                page = f"{len(items)} {offset // LIST_PAGE_SIZE + 1}/{max(1, (len(items) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)}"
                self.header(title, status or page)
                for idx, item in enumerate(visible, offset):
                    self.pixel_row(str(item.get("label", "")), str(item.get("detail", "")), idx == selected)
                self.footer(footer)
                key = read_button()
                if key in {"up", "left"}:
                    selected = (selected - 1) % len(items)
                elif key in {"down", "right"}:
                    selected = (selected + 1) % len(items)
                elif key in {"select"}:
                    return items[selected]
                elif key in {"back", "quit"}:
                    return None
                elif isinstance(key, str) and key.isdigit():
                    idx = int(key) - 1
                    if 0 <= idx < len(items):
                        return items[idx]

    def draw_keyboard(
        self,
        title: str,
        text: str,
        rows: list[list[str]],
        row: int,
        col: int,
        shift: bool = False,
    ) -> None:
        self.header(title, "ABC" if shift else "abc")
        prompt = text[-SCREEN_WIDTH + 4 :] if text else "_"
        print(fit(prompt, SCREEN_WIDTH))
        print("")
        for r_idx, keys in enumerate(rows):
            parts = []
            for c_idx, key in enumerate(keys):
                label = key
                if len(label) == 1 and shift:
                    label = label.upper()
                selected = r_idx == row and c_idx == col
                cell = f"[{label}]"
                if selected:
                    cell = color(cell, "7;31")
                parts.append(cell)
            print(" ".join(parts))
        self.footer("D-pad move  A key  B back")

    def text_keyboard(self, title: str, url_mode: bool = False, initial: str = "") -> str:
        rows = URL_KEY_ROWS if url_mode else SEARCH_KEY_ROWS
        text = initial
        row = 0
        col = 0
        shift = False
        with RawInput():
            while True:
                row = max(0, min(row, len(rows) - 1))
                col = max(0, min(col, len(rows[row]) - 1))
                self.draw_keyboard(title, text, rows, row, col, shift)
                key = read_button()
                if key == "up":
                    row = (row - 1) % len(rows)
                    col = min(col, len(rows[row]) - 1)
                elif key == "down":
                    row = (row + 1) % len(rows)
                    col = min(col, len(rows[row]) - 1)
                elif key == "left":
                    col = (col - 1) % len(rows[row])
                elif key == "right":
                    col = (col + 1) % len(rows[row])
                elif key in {"back", "quit"}:
                    return ""
                elif key == "select":
                    item = rows[row][col]
                    if item == "OK":
                        return text.strip()
                    if item == "DEL":
                        text = text[:-1]
                    elif item == "SP":
                        text += " "
                    elif item == "CASE":
                        shift = not shift
                    elif item == "YT":
                        text += "https://www.youtube.com/watch?v="
                    elif len(item) == 1:
                        text += item.upper() if shift else item

    def status_screen(self, title: str, lines: list[str], footer: str = "") -> None:
        self.header(title, self.status_tokens())
        for line in lines:
            print(fit(line, SCREEN_WIDTH))
        if footer:
            self.footer(footer)

    def progress_screen(
        self,
        title: str,
        phase: str,
        step: int,
        detail: str = "",
        progress: str = "",
        start: float | None = None,
    ) -> None:
        self.header(APP_NAME, f"{step}/4")
        print(fit(title, SCREEN_WIDTH))
        print("")
        print(accent(f"[{step}/4] {phase}"))
        if detail:
            print(fit(detail, SCREEN_WIDTH))
        if progress:
            print(fit(progress, SCREEN_WIDTH))
        if start is not None:
            print(dim(f"Elapsed {format_elapsed(start)}"))
        print("")
        print(dim("This can take a bit."))

    def yt_base(self) -> list[str]:
        args = [
            str(YTDLP),
            "--no-warnings",
            "--socket-timeout",
            "20",
            "--js-runtimes",
            f"quickjs:{QJS}",
            # Persist the parsed player JS / signature code: a warm cache cuts a
            # big chunk off the (RAM-bound, multi-minute) extraction on this box.
            "--cache-dir",
            str(CACHE_DIR),
            # android-first: itag 18 comes back with a direct URL (no qjs), much
            # lighter/faster than the web client; web is the fallback.
            "--extractor-args",
            f"youtube:player_client={PLAYER_CLIENTS}",
        ]
        if COOKIES.exists():
            args.extend(["--cookies", str(COOKIES)])
        return args

    def run_yt(self, extra: list[str], timeout: int = 90) -> subprocess.CompletedProcess[str]:
        import shlex

        cmd = self.yt_base() + extra
        log("yt-dlp: " + " ".join(shlex.quote(part) for part in cmd))
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def check_tools(self) -> bool:
        missing = []
        if not YTDLP.exists():
            missing.append(str(YTDLP))
        if not Path(QJS).exists():
            missing.append(QJS)
        if missing:
            self.header("NanoPlay Setup", "missing")
            print("Missing files:")
            for item in missing:
                print(" " + fit(item, SCREEN_WIDTH - 1))
            pause()
            return False
        return True

    def setup_issues(self) -> list[tuple[str, bool]]:
        issues: list[tuple[str, bool]] = []
        if not YTDLP.exists():
            issues.append(("yt-dlp missing", True))
        if not Path(QJS).exists():
            issues.append(("qjs missing", True))
        if not COOKIES.exists():
            issues.append(("cookies missing", False))
        if not Path(MPLAYER_OPK).exists() and not (MPLAYER_DIR / "mplayer").exists():
            issues.append(("MPlayer missing", False))
        return issues

    def maybe_show_setup_status(self) -> None:
        issues = self.setup_issues()
        if not issues:
            return
        signature = "|".join(name for name, _ in issues)
        if self.store.settings.get("setup_warned") == signature:
            return
        self.header("Setup Status", "check")
        print("NanoPlay needs attention:")
        print("")
        for name, required in issues:
            prefix = "!" if required else "-"
            print(f"{prefix} {fit(name, SCREEN_WIDTH - 2)}")
        print("")
        if any(required for _, required in issues):
            print("Required files missing.")
        else:
            print(dim("Playback may need these."))
        self.store.settings["setup_warned"] = signature
        self.store.save()
        pause()

    def main_menu(self) -> None:
        while True:
            item = self.select_menu(
                APP_NAME,
                [
                    {"label": "Search", "value": "search"},
                    {"label": "Watch Again", "detail": str(len(self.store.history)), "value": "history"},
                    {"label": "Favorites", "detail": str(len(self.store.favorites)), "value": "favorites"},
                    {"label": "Watch Later", "detail": str(len(self.store.watch_later)), "value": "later"},
                    {"label": "Test Videos", "detail": "known good", "value": "test"},
                    {"label": "Categories", "value": "categories"},
                    {"label": "Play URL", "value": "url"},
                    {"label": "Settings", "value": "settings"},
                    {"label": "Replay Last", "detail": "quick", "value": "replay"},
                    {"label": "Exit", "value": "exit"},
                ],
                footer="",
                status=self.status_tokens(),
            )
            if item is None:
                return
            choice = item.get("value")
            if choice == "search":
                self.search_prompt()
            elif choice == "history":
                self.video_list("Watch Again", self.store.history)
            elif choice == "favorites":
                self.video_list("Favorites", self.store.favorites)
            elif choice == "later":
                self.video_list("Watch Later", self.store.watch_later)
            elif choice == "test":
                self.video_list("Test Videos", [dict(v) for v in TEST_VIDEOS])
            elif choice == "categories":
                self.categories()
            elif choice == "url":
                self.play_url_prompt()
            elif choice == "settings":
                self.settings()
            elif choice == "replay":
                if self.store.history:
                    self.play_video(self.store.history[0])
                else:
                    self.message("Nothing played yet.", wait=1)
            elif choice == "exit":
                return

    def search_prompt(self) -> None:
        pins = self.store.pinned_searches()
        items: list[dict[str, Any]] = [{"label": "New Search", "detail": "keyboard", "value": "__new__"}]
        for query in pins[:5]:
            items.append({"label": query, "detail": "pin", "value": query})
        for query in self.store.search_history[:5]:
            if query not in [item.get("value") for item in items]:
                items.append({"label": query, "detail": "recent", "value": query})
        item = self.select_menu("Search", items, footer="A choose  B back", status=self.status_tokens())
        if item is None:
            return
        query = str(item.get("value") or "")
        if query == "__new__":
            query = self.text_keyboard("Search")
        if not query:
            return
        self.search(query)

    def search(self, query: str, force: bool = False) -> None:
        self.store.add_search(query)
        count = int(self.store.settings.get("results", 10))
        cached = None if force else self.store.get_search_cache(query, count)
        if cached is not None:
            self.video_list(
                f"Search: {query}",
                cached,
                subtitle="cached",
                on_refresh=lambda: self.search(query, force=True),
            )
            return

        self.header("Searching", self.status_tokens())
        print(fit(query, SCREEN_WIDTH))
        print("")
        print(dim("Asking YouTube..."))
        try:
            result = self.run_yt(
                [
                    f"ytsearch{count}:{query}",
                    "--flat-playlist",
                    "--dump-json",
                    "--ignore-errors",
                ],
                timeout=75,
            )
        except subprocess.TimeoutExpired:
            self.message("Search timed out.")
            return

        if result.returncode != 0:
            log("search stderr: " + result.stderr[-1200:])
            self.message("Search failed. Check WiFi and yt-dlp.")
            return

        videos: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            url = data.get("webpage_url") or data.get("url") or ""
            vid = data.get("id") or ""
            if url and not str(url).startswith("http") and vid:
                url = f"https://www.youtube.com/watch?v={vid}"
            if not url and vid:
                url = f"https://www.youtube.com/watch?v={vid}"
            if not url:
                continue
            videos.append(
                {
                    "title": data.get("title", "Unknown"),
                    "url": url,
                    "id": vid,
                    "channel": data.get("channel") or data.get("uploader") or "Unknown",
                    "duration": data.get("duration"),
                    "is_live": data.get("is_live"),
                    "live_status": data.get("live_status"),
                }
            )

        self.last_results = videos
        self.store.set_search_cache(query, count, videos)
        self.video_list(
            f"Search: {query}",
            videos,
            on_refresh=lambda: self.search(query, force=True),
        )

    def categories(self) -> None:
        while True:
            items = [{"label": "Test Videos", "detail": "known good", "value": "__test__"}]
            items.extend({"label": name, "value": query} for name, query in CATEGORIES)
            item = self.select_menu("Categories", items, footer="A open  B back", status=self.status_tokens())
            if item is None:
                return
            if item.get("value") == "__test__":
                self.video_list("Test Videos", [dict(v) for v in TEST_VIDEOS])
            else:
                self.search(str(item.get("value") or ""))

    def video_list(
        self,
        title: str,
        videos: list[dict[str, Any]],
        subtitle: str = "",
        on_refresh: Any | None = None,
    ) -> None:
        if not videos:
            self.header(title, "empty")
            print("No videos here yet.")
            print("")
            print(dim("Search, play, or save some."))
            pause()
            return

        selected = 0
        offset = 0
        with RawInput():
            while True:
                if selected < offset:
                    offset = selected
                if selected >= offset + LIST_PAGE_SIZE:
                    offset = selected - LIST_PAGE_SIZE + 1
                visible = videos[offset : offset + LIST_PAGE_SIZE]
                total_pages = max(1, (len(videos) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
                page = offset // LIST_PAGE_SIZE + 1
                status = f"{len(videos)} {page}/{total_pages}"
                self.header(title, status)
                if subtitle:
                    print(dim(fit(subtitle, SCREEN_WIDTH)))
                    print("")

                for idx, video in enumerate(visible, offset):
                    fav = "*" if self.store.is_favorite(video) else " "
                    later = "+" if self.store.is_watch_later(video) else " "
                    prefix = fav + later
                    name = fit(video.get("title"), SCREEN_WIDTH - 5)
                    self.pixel_row(f"{prefix} {name}", selected=(idx == selected))

                    meta = fit(video.get("channel"), 15)
                    duration = format_duration(video.get("duration"))
                    badges = []
                    if self.store.is_known_good(video):
                        badges.append("ok")
                    if self.store.failure_for(video):
                        badges.append("bad")
                    parts = [part for part in (meta, duration, " ".join(badges)) if part]
                    print("  " + dim(fit(" ".join(parts), SCREEN_WIDTH - 2)))

                footer = "A play B back F fav W later X info"
                if total_pages > 1:
                    footer = "U/D sel L/R page A play"
                if on_refresh is not None:
                    footer = "A play X info S refresh B back"
                self.footer(footer)

                key = read_button()
                if key == "up":
                    selected = (selected - 1) % len(videos)
                elif key == "down":
                    selected = (selected + 1) % len(videos)
                elif key == "left":
                    selected = max(0, selected - LIST_PAGE_SIZE)
                elif key == "right":
                    selected = min(len(videos) - 1, selected + LIST_PAGE_SIZE)
                elif key == "select":
                    if self.store.failure_for(videos[selected]):
                        self.message("Retrying a failed video.", wait=1)
                    self.play_video(videos[selected])
                elif key == "favorite":
                    added = self.store.toggle_favorite(videos[selected])
                    self.message("Favorited." if added else "Unfavorited.", wait=1)
                elif key == "watch":
                    added = self.store.toggle_watch_later(videos[selected])
                    self.message("Saved for later." if added else "Removed from later.", wait=1)
                elif key == "info":
                    self.show_info(videos[selected])
                elif key == "search" and on_refresh is not None:
                    on_refresh()
                    return
                elif key in {"back", "quit"}:
                    return

    def show_info(self, video: dict[str, Any]) -> None:
        url = video_url(video)
        cached = self.store.get_metadata(url) if url else None
        if cached:
            info = cached
        else:
            self.header("Video Info", self.status_tokens())
            print(fit(video.get("title"), SCREEN_WIDTH))
            print("")
            print(dim("Loading metadata..."))
            info = self.probe_video(url)
            if info is not None:
                self.store.set_metadata(url, info)

        self.header("Video Info", self.status_tokens())
        if info is None:
            print("Could not load metadata.")
        else:
            print(fit(info.get("title") or video.get("title"), SCREEN_WIDTH))
            print("")
            print("Channel: " + fit(info.get("channel") or video.get("channel"), 19))
            print(f"Height:  {info.get('height', 'unknown')}p")
            print(f"Format:  {info.get('format_id', 'unknown')}")
            duration = format_duration(info.get("duration") or video.get("duration"))
            if duration:
                print(f"Length:  {duration}")
            fail = self.store.failure_for(video)
            if fail:
                print(warn_text("Last failed: " + fit(fail.get("reason"), 14)))
            print("")
            print(dim("Player: " + PLAYER_LABEL))
        pause()

    def fast_probe_video(self, url: str) -> dict[str, Any] | None:
        """Resolve a normal YouTube video through Android Innertube.

        This avoids starting yt-dlp's full extractor on the Nano's tiny CPU.
        It returns the combined itag 18 media URL when YouTube exposes it, and
        callers fall back to yt-dlp for anything unusual.
        """
        import urllib.request

        video_id = youtube_video_id(url)
        if not video_id:
            return None
        body = {
            "context": {"client": FAST_YOUTUBE_CLIENT},
            "videoId": video_id,
            "contentCheckOk": True,
            "racyCheckOk": True,
        }
        request = urllib.request.Request(
            "https://www.youtube.com/youtubei/v1/player?prettyPrint=false",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "User-Agent": FAST_YOUTUBE_CLIENT["userAgent"],
            },
        )
        start = time.time()
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
        except Exception as exc:
            log(f"fast probe failed for {video_id}: {exc}")
            return None

        status = data.get("playabilityStatus") or {}
        if status.get("status") not in (None, "OK"):
            log(f"fast probe status for {video_id}: {status.get('status')} {status.get('reason')}")
            return None
        streaming = data.get("streamingData") or {}
        formats = [f for f in streaming.get("formats", []) if isinstance(f, dict)]
        chosen = next((f for f in formats if f.get("itag") == 18 and f.get("url")), None)
        if chosen is None:
            chosen = next(
                (
                    f
                    for f in formats
                    if f.get("url")
                    and "video/mp4" in str(f.get("mimeType") or "")
                    and int(f.get("height") or 9999) <= MAX_HEIGHT
                    and f.get("audioQuality")
                ),
                None,
            )
        if chosen is None:
            log(f"fast probe no combined mp4 for {video_id}")
            return None

        details = data.get("videoDetails") or {}
        micro = ((data.get("microformat") or {}).get("playerMicroformatRenderer") or {})
        headers = dict(chosen.get("http_headers") or {})
        headers.setdefault("User-Agent", FAST_YOUTUBE_CLIENT["userAgent"])
        info = {
            "id": video_id,
            "title": details.get("title") or micro.get("title", {}).get("simpleText") or video_id,
            "channel": details.get("author") or micro.get("ownerChannelName"),
            "duration": parse_duration_seconds(details.get("lengthSeconds")),
            "height": chosen.get("height"),
            "format_id": str(chosen.get("itag") or ""),
            "url": chosen.get("url"),
            "http_headers": headers,
            "content_length": chosen.get("contentLength"),
            "is_live": bool(details.get("isLiveContent")),
            "live_status": "is_live" if details.get("isLiveContent") else None,
            "fast_resolve_sec": round(time.time() - start, 2),
        }
        log(f"fast probe ok {video_id} {info['format_id']} {info['fast_resolve_sec']}s")
        return info

    def resolve_playable_info(self, url: str) -> dict[str, Any] | None:
        info = self.fast_probe_video(url)
        if info is not None:
            return info
        return self.probe_video(url)

    def probe_video(self, url: str) -> dict[str, Any] | None:
        if not url:
            return None
        try:
            result = self.run_yt(
                [
                    "--no-playlist",
                    "--skip-download",
                    "--dump-single-json",
                    "-f",
                    FORMAT_PLAYBACK,
                    "-S",
                    FORMAT_SORT,
                    url,
                ],
                # Extraction is RAM-bound on this device and can take minutes.
                timeout=480,
            )
        except subprocess.TimeoutExpired:
            log("probe timed out")
            return None

        if result.returncode != 0:
            log("probe stderr: " + result.stderr[-1600:])
            return None

        try:
            info = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            log(f"probe json error: {exc}")
            return None

        # Enforce the height cap on the chosen VIDEO stream.
        vfmt, _ = self._split_av(info)
        height = (vfmt or info).get("height")
        try:
            if height is not None and int(height) > MAX_HEIGHT:
                log(f"rejecting over-{MAX_HEIGHT}p format: {height}")
                return None
        except (TypeError, ValueError):
            pass
        return info

    def _split_av(self, info: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Return (video_format, audio_format) from a probed info dict.

        itag 18 is a single combined stream, so audio is None (MPlayer reads
        both tracks from the one FIFO). The two-format branch is kept only as a
        safety net in case yt-dlp ever returns a merged video+audio pair.
        """
        rf = info.get("requested_formats")
        if rf:
            combined = next(
                (
                    f
                    for f in rf
                    if f.get("vcodec") not in (None, "none")
                    and f.get("acodec") not in (None, "none")
                ),
                None,
            )
            if combined:
                return combined, None
            v = next((f for f in rf if f.get("vcodec") not in (None, "none")), None)
            a = next((f for f in rf if f.get("acodec") not in (None, "none")), None)
            return v, a
        if info.get("url"):
            return info, None
        return None, None

    def single_stream_source(self, info: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        vfmt, afmt = self._split_av(info)
        if not vfmt or afmt is not None:
            return None
        media_url = str(vfmt.get("url") or "")
        if not media_url:
            return None
        headers: dict[str, Any] = {}
        for block in (info.get("http_headers"), vfmt.get("http_headers")):
            if isinstance(block, dict):
                headers.update(block)
        headers.pop("Accept-Encoding", None)
        return media_url, headers

    def should_stream_request(self, req: dict[str, Any]) -> tuple[bool, str]:
        mode = self.playback_mode()
        if mode == "stream":
            return True, "stream setting"
        if mode == "cache":
            return False, "cache setting"

        live_status = str(req.get("live_status") or "").lower()
        if bool(req.get("is_live")) or live_status in {"is_live", "post_live", "is_upcoming"}:
            return True, "live video"

        duration = parse_duration_seconds(req.get("duration"))
        if duration >= LONG_VIDEO_STREAM_SECONDS:
            return True, f"{format_duration(duration)} long"
        if duration <= 0:
            return True, "unknown length"
        return False, f"{format_duration(duration)} clip"

    def load_failed(self, message: str) -> None:
        self.header("Load Failed", self.status_tokens())
        print(fit(message, SCREEN_WIDTH))
        print("")
        print("Try Settings > Player")
        print("auto / cache / stream")
        print("")
        print("Cookies may need refresh.")
        time.sleep(4)

    def _start_feeder(self, url: str, headers: dict[str, Any], fifo: Path) -> subprocess.Popen[Any]:
        import shlex

        """Stream an https media URL into a FIFO via a tiny urllib copy.

        Keeps the heavy yt-dlp process out of the playback path (RAM) and
        provides TLS through Python's ssl, which MPlayer's ffmpeg lacks. The
        shell opens the FIFO for write (blocking) before exec'ing python, so the
        network connection only opens once MPlayer opens the FIFO for read.
        """
        streamer = [
            "python3",
            "-u",
            "-c",
            (
                "import sys,os,json,urllib.request,shutil\n"
                "req=urllib.request.Request(os.environ['YT_URL'],"
                "headers=json.loads(os.environ.get('YT_HDR','{}')))\n"
                "shutil.copyfileobj(urllib.request.urlopen(req,timeout=30),sys.stdout.buffer,65536)\n"
            ),
        ]
        shell = " ".join(shlex.quote(part) for part in streamer) + " > " + shlex.quote(str(fifo))
        env = dict(os.environ, YT_URL=str(url), YT_HDR=json.dumps(headers or {}))
        feeder_log = WORK_DIR / (fifo.name + ".log")
        return subprocess.Popen(
            ["sh", "-c", shell],
            stdout=open(feeder_log, "ab"),
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
            env=env,
        )

    def play_url_prompt(self) -> None:
        url = self.text_keyboard("Play URL", url_mode=True)
        if not url:
            return
        self.play_video({"title": url, "url": url, "channel": "Manual URL"})

    def play_video(self, video: dict[str, Any]) -> None:
        """Menu mode: hand the selection to the standalone player and exit.

        The menu runs inside `st`, which owns the one SDL framebuffer + input
        device -- MPlayer can't share them (black screen, buttons leak into the
        terminal). So we don't play here: record the request and exit cleanly so
        `st` closes; run.sh then plays it WITHOUT `st` (MPlayer owns the screen
        and buttons, like the NRK player) and relaunches the menu afterwards.
        """
        url = str(video.get("url", ""))
        if not url:
            self.message("Missing URL.")
            return
        self.store.add_history(video)
        try:
            PLAY_REQUEST.write_text(
                json.dumps(
                    {
                        "url": url,
                        "title": video.get("title") or url,
                        "channel": video.get("channel") or "",
                        "id": video.get("id") or "",
                        "duration": video.get("duration"),
                        "is_live": video.get("is_live"),
                        "live_status": video.get("live_status"),
                    }
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            self.message(f"Could not start playback: {exc}")
            return
        raise SystemExit(0)

    def run_play_mode(self) -> int:
        import signal

        """Standalone player (no `st`): short clips download first for smooth
        playback; long/live/unknown clips stream through a FIFO so huge videos
        can start without waiting for the entire file. MPlayer inherits the
        console and owns the framebuffer + buttons.
        """
        try:
            req = json.loads(PLAY_REQUEST.read_text(encoding="utf-8"))
        except Exception:
            return 0
        url = str(req.get("url") or "")
        title = req.get("title") or url
        channel = req.get("channel") or ""
        # Clear any stray MPlayer from a prior stuck attempt: two instances fight
        # over the single SPI framebuffer and render black.
        subprocess.run(["killall", "-9", "mplayer"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not url or not self.ensure_mplayer():
            if not url:
                return 0
            print("MPlayer.opk not found.")
            time.sleep(2)
            return 1

        WORK_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        vfile = CACHE_DIR / "stream_v.dat"
        input_conf = WORK_DIR / "input.conf"
        # Persistent (survives the youtube.sh cleanup that wipes /tmp/youtube_nano)
        # so the play phase is debuggable after the fact.
        player_log = APP_DIR / "play.log"
        try:
            player_log.write_text(
                f"[{now()}] play start TERM={os.environ.get('TERM')} "
                f"TTY={os.ttyname(0) if os.isatty(0) else 'notty'} "
                f"fb0={'yes' if os.path.exists('/dev/fb0') else 'no'} pgid={os.getpgrp()}\n",
                encoding="utf-8",
            )
        except Exception:
            pass

        # Instrumentation: log if/when we get a termination signal (to find what
        # is killing the player -- suspected gmenu2x reclaiming the framebuffer).
        def _onsig(signum, _frame):
            try:
                with player_log.open("a", encoding="utf-8") as fh:
                    fh.write(f"[{now()}] PYTHON received signal {signum}\n")
            except Exception:
                pass
            raise SystemExit(128 + signum)

        for _s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            try:
                signal.signal(_s, _onsig)
            except Exception:
                pass

        if vfile.exists():
            vfile.unlink()
        input_conf.write_text(
            "\n".join(
                [
                    "q quit 0",
                    "a pause",
                    "b quit 0",
                    "d volume -1",
                    "u volume 1",
                    "i seek +30",
                    "j seek -10",
                    "h switch_ratio 1",
                    "o speed_mult 1.1",
                    "v speed_mult 0.9091",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        display_title = str(title)
        if channel:
            display_title = f"{title} / {channel}"
        stream, reason = self.should_stream_request(req)
        with player_log.open("a", encoding="utf-8") as fh:
            fh.write(f"[{now()}] playback mode={self.playback_mode()} stream={stream} reason={reason}\n")

        if stream:
            return self.stream_then_play(url, display_title, input_conf, player_log, reason)
        return self.download_then_play(url, display_title, input_conf, player_log, vfile)

    def mplayer_cmd(self, input_conf: Path, media: Path | str, stream: bool = False) -> list[str]:
        cache_size = "8192" if stream else "2048"
        cache_min = "2" if stream else "3"
        return [
            "nice",
            "-n",
            "-10",
            "./mplayer",
            # NO -slave: in slave mode MPlayer reads commands from stdin, and our
            # stdin is a non-tty that never delivers data/EOF, so the main loop
            # blocked there and playback froze (black screen). Without slave mode
            # MPlayer reads the buttons via SDL and the -input conf bindings
            # (q/b/MENU -> quit) work directly.
            "-input",
            f"conf={input_conf}",
            # cache=4: no "Cache fill" status lines (they grew play.log to 16 MB)
            "-msglevel",
            "statusline=0:cache=4",
            "-cache",
            cache_size,
            "-cache-min",
            cache_min,
            "-demuxer",
            "lavf",
            # NRK's proven scaler: 360p -> 240x135 letterbox on the 240px screen.
            "-vf",
            "scale=240:135",
            "-sws",
            "9",
            # RG Nano SDL only accepts S16; the AAC decoder emits float.
            "-af",
            "format=s16le",
            "-vo",
            "sdl",
            "-ao",
            "sdl",
            "-fs",
            "-framedrop",
            str(media),
        ]

    def play_with_mplayer(
        self,
        media: Path | str,
        input_conf: Path,
        player_log: Path,
        title: str,
        phase: str = "Playing",
        stream: bool = False,
    ) -> int:
        self.progress_screen(str(title), phase, 4, "MENU / B / q exits", "", time.time())
        mplayer_cmd = self.mplayer_cmd(input_conf, media, stream=stream)
        # Black-screen fix: MPlayer's SDL fbcon does VT_ACTIVATE+VT_WAITACTIVE to
        # switch to a free VT, and blocks forever (wchan __vt_event_wait) unless
        # the kernel can make that switch; _reset_vts() says what stops it.
        self._reset_vts()

        with player_log.open("a", encoding="utf-8") as fh:
            fh.write(f"[{now()}] launching mplayer (vts reset) media={media}\n")
        with player_log.open("ab") as output:
            mp_rc = subprocess.call(
                mplayer_cmd,
                cwd=str(MPLAYER_DIR),
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        with player_log.open("a", encoding="utf-8") as fh:
            fh.write(f"[{now()}] mplayer rc={mp_rc} (negative = killed by signal -rc)\n")
        return mp_rc

    def download_direct_with_progress(
        self,
        media_url: str,
        headers: dict[str, Any],
        output: Path,
        title: str,
        player_log: Path,
        total_bytes: Any = None,
    ) -> bool:
        start = time.time()
        done = 0
        last_draw = 0.0
        try:
            total = int(total_bytes or 0)
        except (TypeError, ValueError):
            total = 0
        self.progress_screen(title, "Downloading", 2, "Fast direct URL", "", start)
        try:
            import urllib.request

            request = urllib.request.Request(media_url, headers=headers or {})
            with urllib.request.urlopen(request, timeout=30) as response, output.open("wb") as fh:
                if not total:
                    try:
                        total = int(response.headers.get("Content-Length") or 0)
                    except (TypeError, ValueError):
                        total = 0
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    now_time = time.time()
                    if now_time - last_draw >= 0.5:
                        if total:
                            detail = f"{format_bytes(done)} / {format_bytes(total)}"
                        else:
                            detail = format_bytes(done)
                        elapsed = max(0.1, now_time - start)
                        speed = format_bytes(int(done / elapsed)) + "/s"
                        self.progress_screen(title, "Downloading", 2, detail, speed, start)
                        last_draw = now_time
        except Exception as exc:
            with player_log.open("a", encoding="utf-8") as fh:
                fh.write(f"[{now()}] direct download failed: {exc}\n")
            return False
        ok = output.exists() and output.stat().st_size > 0
        with player_log.open("a", encoding="utf-8") as fh:
            fh.write(f"[{now()}] direct download ok={ok} bytes={done}\n")
        if ok:
            self.progress_screen(title, "Preparing player", 3, "Direct file ready", "", start)
        return ok

    def download_then_play(
        self,
        url: str,
        display_title: str,
        input_conf: Path,
        player_log: Path,
        vfile: Path,
    ) -> int:
        info = self.fast_probe_video(url)
        source = self.single_stream_source(info) if info else None
        if source is not None:
            media_url, headers = source
            if self.download_direct_with_progress(
                media_url,
                headers,
                vfile,
                display_title,
                player_log,
                (info or {}).get("content_length"),
            ):
                mp_rc = self.play_with_mplayer(vfile, input_conf, player_log, display_title)
                if mp_rc == 0:
                    self.store.mark_success(url)
                else:
                    self.store.mark_failed(url, f"player rc {mp_rc}")
                if vfile.exists():
                    vfile.unlink()
                return 0
            if vfile.exists():
                vfile.unlink()

        # Fallback: full yt-dlp download. This is slower but handles odd videos
        # that the lightweight Android player endpoint cannot resolve.
        dl = self.yt_base() + [
            "--no-playlist",
            "--newline",
            "--no-part",
            "-f",
            FORMAT_PLAYBACK,
            "-S",
            FORMAT_SORT,
            "-o",
            str(vfile),
            url,
        ]
        dl_rc = self.download_with_progress(dl, display_title, player_log)
        ok = dl_rc == 0 and vfile.exists() and vfile.stat().st_size > 0
        with player_log.open("a", encoding="utf-8") as fh:
            fh.write(f"\n[{now()}] download rc={dl_rc} size={vfile.stat().st_size if vfile.exists() else 0}\n")
        if not ok:
            if vfile.exists():
                vfile.unlink()
            self.store.mark_failed(url, "download failed")
            self.load_failed("Could not load video.")
            return 1

        mp_rc = self.play_with_mplayer(vfile, input_conf, player_log, display_title)
        if mp_rc == 0:
            self.store.mark_success(url)
        else:
            self.store.mark_failed(url, f"player rc {mp_rc}")
        if vfile.exists():
            vfile.unlink()
        return 0

    def stream_then_play(
        self,
        url: str,
        display_title: str,
        input_conf: Path,
        player_log: Path,
        reason: str,
    ) -> int:
        start = time.time()
        self.progress_screen(display_title, "Resolving stream", 1, "Getting direct media URL", reason, start)
        info = self.resolve_playable_info(url)
        if info is None:
            self.store.mark_failed(url, "stream probe failed")
            self.load_failed("Could not open stream.")
            return 1
        self.store.set_metadata(url, info)
        source = self.single_stream_source(info)
        if source is None:
            self.store.mark_failed(url, "no single stream")
            self.load_failed("No single 360p stream.")
            return 1

        media_url, headers = source
        fifo = WORK_DIR / "stream_v.fifo"
        if fifo.exists():
            fifo.unlink()
        try:
            os.mkfifo(fifo)
        except (AttributeError, OSError) as exc:
            with player_log.open("a", encoding="utf-8") as fh:
                fh.write(f"[{now()}] mkfifo failed: {exc}\n")
            self.store.mark_failed(url, "fifo failed")
            self.load_failed("Could not make stream pipe.")
            return 1

        self.progress_screen(display_title, "Opening stream", 2, "No full download", reason, start)
        feeder = self._start_feeder(media_url, headers, fifo)
        with player_log.open("a", encoding="utf-8") as fh:
            fh.write(f"[{now()}] feeder pid={feeder.pid} reason={reason}\n")
        try:
            mp_rc = self.play_with_mplayer(fifo, input_conf, player_log, display_title, phase="Streaming", stream=True)
        finally:
            self.stop_proc(feeder)
            if fifo.exists():
                fifo.unlink()

        if mp_rc == 0:
            self.store.mark_success(url)
        else:
            self.store.mark_failed(url, f"stream rc {mp_rc}")
        return 0

    @staticmethod
    def _reset_vts() -> None:
        """Let MPlayer's SDL switch to a VT of its own (else: black screen, about
        1 s of audio, and MPlayer stuck in __vt_event_wait for good).

        SDL's fbcon driver opens a free VT, asks for a switch to it and waits.
        The kernel ignores that switch while a VT is in VT_PROCESS mode with an
        owner that doesn't answer (st can leave one so), while the active VT is
        VT_AUTO in graphics mode (an SDL program that exits can leave it so),
        and while VT switching is locked. So: every VT to VT_AUTO, the active
        one to text mode, switching unlocked. The same goes for st after a
        video, so run.sh calls this (`vtreset`) before each phase. Don't lock
        switching again afterwards: st then hangs the same way (black screen,
        frozen; seen 2026-10-31 on the stock image)."""
        try:
            import fcntl
            import struct
        except ImportError:
            return
        VT_SETMODE, VT_GETSTATE, VT_UNLOCKSWITCH = 0x5602, 0x5603, 0x560C
        KDSETMODE, KD_TEXT = 0x4B3A, 0
        vt_auto = struct.pack("BBhhh", 0, 0, 0, 0, 0)  # mode=VT_AUTO
        for n in range(1, 7):
            try:
                fd = os.open("/dev/tty%d" % n, os.O_RDWR | os.O_NOCTTY)
                try:
                    fcntl.ioctl(fd, VT_SETMODE, vt_auto)
                finally:
                    os.close(fd)
            except OSError:
                pass
        try:
            fd = os.open("/dev/tty0", os.O_RDWR | os.O_NOCTTY)
            try:
                state = bytearray(6)
                fcntl.ioctl(fd, VT_GETSTATE, state, True)
                active = struct.unpack("HHH", state)[0]
                fcntl.ioctl(fd, VT_UNLOCKSWITCH, 0)
            finally:
                os.close(fd)
            fd = os.open("/dev/tty%d" % active, os.O_RDWR | os.O_NOCTTY)
            try:
                fcntl.ioctl(fd, KDSETMODE, KD_TEXT)
            finally:
                os.close(fd)
        except OSError:
            pass

    def _buffer_wait(
        self,
        path: Path,
        feeder: subprocess.Popen[Any],
        min_bytes: int,
        timeout: int,
    ) -> bool:
        """Block until `path` holds a head-start buffer, or the feeder finishes.

        Returns True once `path` reaches min_bytes, or the feeder exits having
        written something (short video fully downloaded). False if nothing was
        downloaded within `timeout` or the feeder died empty.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            size = path.stat().st_size if path.exists() else 0
            if size >= min_bytes:
                return True
            if feeder.poll() is not None:
                return size > 0
            time.sleep(0.5)
        return path.exists() and path.stat().st_size > 0

    def ensure_mplayer(self) -> bool:
        if (MPLAYER_DIR / "mplayer").exists():
            return True
        if not Path(MPLAYER_OPK).exists():
            log(f"missing {MPLAYER_OPK}")
            return False
        MPLAYER_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(["mount", "-o", "loop", MPLAYER_OPK, str(MPLAYER_DIR)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return (MPLAYER_DIR / "mplayer").exists()

    def stop_proc(self, proc: subprocess.Popen[Any] | None) -> None:
        import signal

        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            proc.wait(timeout=2)
        except Exception:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def progress_from_line(self, line: str) -> tuple[int, str, str, str] | None:
        text = line.strip()
        if not text:
            return None
        if text.startswith("[download] Destination:"):
            return (2, "Downloading", "Opening file", "")
        if text.startswith("[download]") and "%" in text:
            percent = ""
            total = ""
            eta = ""
            speed = ""
            m = re.search(r"\[download\]\s+([0-9.]+)%\s+of\s+([^ ]+)", text)
            if m:
                percent = m.group(1) + "%"
                total = m.group(2)
            m = re.search(r"ETA\s+([^ ]+)", text)
            if m:
                eta = "ETA " + m.group(1)
            m = re.search(r"at\s+([^ ]+/s)", text)
            if m:
                speed = m.group(1)
            detail = f"{percent} of {total}" if percent and total else "Downloading"
            progress = " ".join(part for part in (speed, eta) if part)
            return (2, "Downloading", detail, progress)
        if text.startswith("[youtube]"):
            clean = text.replace("[youtube]", "YouTube", 1)
            if "Downloading webpage" in text:
                return (1, "Resolving video", "Downloading page", "")
            if "Downloading player" in text:
                return (1, "Resolving video", "Warming player cache", "")
            if "Extracting URL" in text:
                return (1, "Resolving video", "Reading video page", "")
            return (1, "Resolving video", fit(clean, SCREEN_WIDTH), "")
        if "has already been downloaded" in text:
            return (2, "Downloading", "Already cached", "")
        if "Merging formats" in text:
            return (3, "Preparing", "Finishing file", "")
        return None

    def download_with_progress(self, cmd: list[str], title: str, player_log: Path) -> int:
        import shlex

        start = time.time()
        step = 1
        phase = "Resolving video"
        detail = "Contacting YouTube"
        progress = ""
        last_draw = 0.0
        self.progress_screen(title, phase, step, detail, progress, start)

        with player_log.open("a", encoding="utf-8") as fh:
            fh.write(f"[{now()}] download command: {' '.join(shlex.quote(p) for p in cmd)}\n")
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
            except OSError as exc:
                fh.write(f"[{now()}] download launch failed: {exc}\n")
                self.progress_screen(title, "Download failed", 2, str(exc), "", start)
                return 127

            if proc.stdout is not None:
                for raw in proc.stdout:
                    for line in raw.replace("\r", "\n").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        fh.write(line + "\n")
                        parsed = self.progress_from_line(line)
                        if parsed is not None:
                            step, phase, detail, progress = parsed
                        now_time = time.time()
                        if now_time - last_draw >= 0.5 or step >= 2:
                            self.progress_screen(title, phase, step, detail, progress, start)
                            last_draw = now_time
                proc.stdout.close()

            rc = proc.wait()
            fh.write(f"[{now()}] download process rc={rc}\n")

        if rc == 0:
            self.progress_screen(title, "Preparing player", 3, "Almost there", "", start)
        else:
            self.progress_screen(title, "Download failed", 2, f"yt-dlp rc {rc}", "", start)
        return rc

    def update_ytdlp(self) -> None:
        self.header("Update yt-dlp", self.status_tokens())
        print("Updating...")
        print("")
        try:
            result = self.run_yt(["-U"], timeout=180)
        except subprocess.TimeoutExpired:
            self.message("Update timed out.")
            return
        if result.returncode == 0:
            self.store.settings["yt_dlp_version"] = "updated " + now().split("T")[0]
            self.store.save()
        self.header("Update Result", self.status_tokens())
        output = (result.stdout + result.stderr).strip()[-520:] or "No output."
        for line in output.splitlines()[-9:]:
            print(fit(line, SCREEN_WIDTH))
        pause()

    def settings(self) -> None:
        while True:
            version = str(self.store.settings.get("yt_dlp_version", "installed"))
            count = int(self.store.settings.get("results", 10))
            player_mode = self.playback_mode_label()
            item = self.select_menu(
                "Settings",
                [
                    {"label": "yt-dlp", "detail": fit(version, 10), "value": "noop"},
                    {"label": "Cookies", "detail": "OK" if COOKIES.exists() else "missing", "value": "noop"},
                    {"label": "Results", "detail": str(count), "value": "results"},
                    {"label": "Player", "detail": player_mode, "value": "player"},
                    {"label": "Update yt-dlp", "value": "update"},
                    {"label": "Clear history", "value": "clear_history"},
                    {"label": "Clear cache", "value": "clear_cache"},
                    {"label": "Clear failures", "value": "clear_failures"},
                    {"label": "Nano Lab", "detail": "advanced", "value": "lab"},
                    {"label": "About", "value": "about"},
                ],
                footer="A choose  B back",
                status=self.status_tokens(),
            )
            if item is None:
                return
            choice = item.get("value")
            if choice == "noop":
                self.message("Status item.", wait=1)
            elif choice == "update":
                self.update_ytdlp()
            elif choice == "results":
                current = int(self.store.settings.get("results", 10))
                choices = [5, 10, 15, 20]
                next_idx = (choices.index(current) + 1) % len(choices) if current in choices else 1
                self.store.settings["results"] = choices[next_idx]
                self.store.save()
                self.message(f"Results: {choices[next_idx]}", wait=1)
            elif choice == "player":
                current = self.playback_mode()
                next_idx = (PLAYBACK_MODES.index(current) + 1) % len(PLAYBACK_MODES)
                self.store.settings["playback_mode"] = PLAYBACK_MODES[next_idx]
                self.store.save()
                self.message("Player: " + self.playback_mode_label(), wait=1)
            elif choice == "clear_history":
                self.store.clear_history()
                self.message("History cleared.", wait=1)
            elif choice == "clear_cache":
                self.store.clear_cache_files()
                self.message("Cache cleared.", wait=1)
            elif choice == "clear_failures":
                self.store.clear_failures()
                self.message("Failures cleared.", wait=1)
            elif choice == "lab":
                self.experimental_menu()
            elif choice == "about":
                self.about()

    def experimental_menu(self) -> None:
        while True:
            cache_items = len(self.store.search_cache) + len(self.store.metadata_cache)
            fail_items = len(self.store.failures)
            known_items = len(self.store.known_good)
            item = self.select_menu(
                "Nano Lab",
                [
                    {"label": "Color", "detail": "on" if self.store.settings.get("color", True) else "off", "value": "color"},
                    {"label": "Caches", "detail": str(cache_items), "value": "noop"},
                    {"label": "Failures", "detail": str(fail_items), "value": "noop"},
                    {"label": "Known good", "detail": str(known_items), "value": "noop"},
                    {"label": "Reset setup warn", "value": "setup"},
                    {"label": "Show paths", "value": "paths"},
                    {"label": "Pinned searches", "value": "pins"},
                ],
                footer="A choose  B back",
                status=self.status_tokens(),
            )
            if item is None:
                return
            choice = item.get("value")
            if choice == "noop":
                self.message("Status item.", wait=1)
            elif choice == "color":
                current = bool(self.store.settings.get("color", True))
                self.store.settings["color"] = not current
                self.store.save()
                global USE_COLOR
                USE_COLOR = not current
                self.message("Color " + ("on" if USE_COLOR else "off"), wait=1)
            elif choice == "setup":
                self.store.settings.pop("setup_warned", None)
                self.store.save()
                self.message("Setup warning reset.", wait=1)
            elif choice == "paths":
                self.header("Paths", self.status_tokens())
                for item in (APP_DIR, DATA_DIR, CACHE_DIR, MPLAYER_OPK):
                    print(fit(str(item), SCREEN_WIDTH))
                pause()
            elif choice == "pins":
                self.header("Pinned", self.status_tokens())
                for idx, query in enumerate(self.store.pinned_searches(), 1):
                    print(f"{idx} {fit(query, SCREEN_WIDTH - 2)}")
                pause()

    def about(self) -> None:
        self.header("About", self.status_tokens())
        print(APP_NAME)
        print(dim(APP_TAGLINE))
        print("")
        print("YouTube for RG Nano")
        print("Video/audio verified smooth.")
        print("Player: " + PLAYER_LABEL)
        print("Mode:   " + self.playback_mode_label())
        print("")
        print(dim("Menu in st-sdl; playback"))
        print(dim("outside st so MPlayer owns"))
        print(dim("the screen and buttons."))
        pause()

    def preview_video_list(self, title: str, videos: list[dict[str, Any]], subtitle: str = "") -> None:
        self.header(title, f"{len(videos)} 1/1")
        if subtitle:
            print(dim(fit(subtitle, SCREEN_WIDTH)))
            print("")
        for local_idx, video in enumerate(videos[:LIST_PAGE_SIZE], 1):
            fav = "*" if local_idx == 3 else " "
            later = "+" if local_idx == 2 else " "
            self.pixel_row(f"{fav}{later} {fit(video.get('title'), SCREEN_WIDTH - 5)}", selected=(local_idx == 1))
            duration = format_duration(video.get("duration"))
            meta = "  ".join(part for part in (fit(video.get("channel"), 16), duration, video.get("badge", "")) if part)
            print("  " + dim(fit(meta, SCREEN_WIDTH - 2)))
        self.footer("A play F fav W later X info")

    def preview_screens(self) -> None:
        global USE_COLOR
        if os.environ.get("NO_COLOR") is not None:
            USE_COLOR = False
        sample = [
            {"title": "Tiny consoles in 2026", "channel": "Retro Game Corps", "duration": 764, "badge": "ok"},
            {"title": "RG Nano deep dive and tips", "channel": "TechDweeb", "duration": 558},
            {"title": "Best micro games for trips", "channel": "WULFF DEN", "duration": 962, "badge": "ok"},
            {"title": "Lofi beats for a tiny screen", "channel": "Lofi Girl", "duration": 0},
        ]

        self.header(APP_NAME, "CK WARM")
        for idx, (label, detail) in enumerate(
            [
                ("Search", ""),
                ("Watch Again", "12"),
                ("Favorites", "4"),
                ("Watch Later", "3"),
                ("Test Videos", "ok"),
                ("Categories", ""),
                ("Play URL", ""),
                ("Settings", ""),
            ]
        ):
            self.pixel_row(label, detail, selected=(idx == 0))
        self.footer("")

        self.preview_video_list("Search: retro", sample, "cached")

        self.header("Settings", "CK WARM")
        for idx, (label, detail) in enumerate(
            [
                ("yt-dlp", "installed"),
                ("Cookies", "OK"),
                ("Results", "10"),
                ("Player", "auto"),
                ("Update yt-dlp", ""),
                ("Clear cache", ""),
                ("Nano Lab", "advanced"),
            ]
        ):
            self.pixel_row(label, detail, selected=(idx == 3))
        self.footer("A choose  B back")

        self.draw_keyboard("Search", "retro hand", SEARCH_KEY_ROWS, 1, 2)

        self.progress_screen("Never Gonna Give You Up / Rick Astley", "Resolving video", 1, "Downloading page", "", time.time() - 38)
        self.progress_screen("Never Gonna Give You Up / Rick Astley", "Downloading", 2, "42.5% of 18.2MiB", "621KiB/s ETA 00:17", time.time() - 72)
        self.progress_screen("Six hour archive", "Opening stream", 2, "No full download", "6:00:00 long", time.time() - 14)

    def message(self, text: str, wait: int = 0) -> None:
        self.header(APP_NAME, self.status_tokens())
        print(fit(text, SCREEN_WIDTH))
        if wait:
            time.sleep(wait)
        else:
            pause()


def main() -> int:
    # run.sh, before each phase: let the next SDL program (st or MPlayer) get
    # its VT (see YouTubeApp._reset_vts)
    if len(sys.argv) > 1 and sys.argv[1] == "vtreset":
        YouTubeApp._reset_vts()
        return 0

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ensure_clock()

    if any(arg in {"preview", "--preview"} for arg in sys.argv[1:]):
        app = YouTubeApp()
        app.preview_screens()
        return 0

    # Phase 2: standalone player (launched by run.sh without `st`).
    if len(sys.argv) > 1 and sys.argv[1] == "play":
        app = YouTubeApp()
        try:
            return app.run_play_mode()
        finally:
            try:
                PLAY_REQUEST.unlink()
            except OSError:
                pass

    # Phase 1: menu (runs inside `st`).
    LOG_FILE.write_text(f"[{now()}] youtube_nano.py start\n", encoding="utf-8")
    try:
        PLAY_REQUEST.unlink()
    except OSError:
        pass
    app = YouTubeApp()
    if not app.check_tools():
        return 1
    try:
        app.main_menu()
    except SystemExit:
        # play_video raises this to hand off to the player phase.
        raise
    finally:
        subprocess.run(["killall", "-TERM", "yt-dlp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
