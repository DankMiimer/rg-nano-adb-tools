"""RetroAchievements offline sync backend for Connect.opk.

Manages credentials (auth.json, token-only), atomic queue flushing to
retroachievements.org, incremental ROM hashing/caching (rom_hashes.json +
cache/sets/m_<md5>.json + cache/unlocks/g_<gid>.json), and UTC clock
normalization via ra_clock.

Stdlib-only so it runs inside Connect.opk's bundled Python 3.12 without
external dependencies and also runs identically in host offline tests.
"""
from __future__ import annotations

import email.utils
import hashlib
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

import ra_clock
import ra_hash

USER_AGENT = "RG-Nano-Connect/1.0 (FunKey-OS; +https://github.com/DankMiimer/drum78-os-usb-wifi-link)"
MAX_BACKDATE_SEC = 14 * 86400
MAX_HISTORY = 100

# Set-fetching throttle keeps us polite to RetroAchievements API limits.
SET_FETCH_DELAY_SEC = float(os.environ.get("RA_FETCH_DELAY", "0.35"))


def ra_dir() -> str:
    return os.environ.get("RA_DATA", "/mnt/FunKey/raoffline")


def roms_root() -> str:
    return os.environ.get("RA_ROMS", "/mnt")


def ra_host() -> str:
    return os.environ.get("RA_HOST", "https://retroachievements.org").rstrip("/")


def auth_path() -> str:
    return os.path.join(ra_dir(), "auth.json")


def hashes_path() -> str:
    return os.path.join(ra_dir(), "rom_hashes.json")


def queue_dir() -> str:
    return os.path.join(ra_dir(), "queue")


def sets_dir() -> str:
    return os.path.join(ra_dir(), "cache", "sets")


def unlocks_dir() -> str:
    return os.path.join(ra_dir(), "cache", "unlocks")


def history_path() -> str:
    return os.path.join(ra_dir(), "history.json")


def sync_meta_path() -> str:
    return os.path.join(ra_dir(), "sync_meta.json")


def login_import_paths() -> list[str]:
    return [
        os.path.join(ra_dir(), "login.txt"),
        os.path.join(roms_root(), "ra_login.txt"),
    ]


def _read_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_atomic(path: str, data: str | bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    mode = "wb" if isinstance(data, bytes) else "w"
    kwargs = {} if isinstance(data, bytes) else {"encoding": "utf-8", "newline": "\n"}
    with open(tmp, mode, **kwargs) as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _write_json(path: str, obj) -> None:
    _write_atomic(path, json.dumps(obj, separators=(",", ":")))


class ApiError(Exception):
    def __init__(self, message: str, code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _format_net_error(exc: Exception) -> str:
    """Turn raw urllib/socket errors into actionable text for the Nano screen."""
    reason = getattr(exc, "reason", exc)
    low = str(exc).lower()
    if (
        isinstance(reason, socket.gaierror)
        or "try again" in low
        or "name or service not known" in low
        or "temporary failure in name resolution" in low
    ):
        pending_flag = os.path.join(os.environ.get("USBLINK_RUN", "/tmp"), "usblink-dhcp-pending")
        if os.path.exists(pending_flag):
            return "USB DHCP failed: use USB -> Reconnect or check PC ICS/Wi-Fi."
        return "No internet/DNS. Check Wi-Fi or USB Reconnect."
    if isinstance(reason, TimeoutError) or isinstance(exc, TimeoutError) or "timed out" in low:
        return "Connection timed out talking to RetroAchievements."
    if "network is unreachable" in low or "no route to host" in low or "connection refused" in low:
        return "No internet connection. Check Wi-Fi or USB."
    return str(exc)


def _http_dorequest(params: dict[str, str], timeout: int = 20) -> tuple[dict, str, int | None]:
    """Call dorequest.php (GET for read calls, POST for login/awards). Returns (json_obj, raw_text, server_utc)."""
    url = f"{ra_host()}/dorequest.php"
    body = urllib.parse.urlencode(params).encode("ascii")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            date_hdr = resp.headers.get("Date")
            server_utc = _parse_http_date(date_hdr)
    except urllib.error.HTTPError as exc:
        retryable = exc.code == 429 or exc.code >= 500
        try:
            detail = exc.read().decode("utf-8", errors="replace")
            obj = json.loads(detail)
            msg = obj.get("Error") or f"HTTP {exc.code}"
        except Exception:
            msg = f"HTTP {exc.code}"
        raise ApiError(msg, code=exc.code, retryable=retryable) from exc
    except Exception as exc:
        raise ApiError(_format_net_error(exc), retryable=True) from exc

    try:
        obj = json.loads(raw)
    except ValueError as exc:
        raise ApiError("Invalid server JSON", retryable=True) from exc
    if not isinstance(obj, dict):
        raise ApiError("Unexpected response shape", retryable=False)
    return obj, raw, server_utc


def _parse_http_date(date_hdr: str | None) -> int | None:
    if not date_hdr:
        return None
    try:
        return int(email.utils.parsedate_to_datetime(date_hdr).timestamp())
    except Exception:
        return None


def sync_utc_clock(timeout: int = 10) -> int | None:
    """Measure true UTC from RA response Date header and persist clock_sync.json."""
    req = urllib.request.Request(
        f"{ra_host()}/dorequest.php?r=latestclient&e=0",
        method="GET",
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            utc = _parse_http_date(resp.headers.get("Date"))
            if utc:
                ra_clock.write_clock_sync(utc)
                return utc
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def get_auth() -> dict | None:
    obj = _read_json(auth_path())
    if isinstance(obj, dict) and obj.get("User") and obj.get("Token"):
        return obj
    return None


def logout() -> None:
    try:
        os.remove(auth_path())
    except OSError:
        pass


def login(username: str, password: str = "", token: str = "") -> dict:
    username = (username or "").strip()
    if not username or (not password and not token):
        raise ApiError("Username and password/token required")
    params = {"r": "login2", "u": username}
    if password:
        params["p"] = password
    else:
        params["t"] = token
    obj, _, server_utc = _http_dorequest(params)
    if not obj.get("Success"):
        raise ApiError(obj.get("Error") or "Login failed")
    if server_utc:
        ra_clock.write_clock_sync(server_utc)
    saved = {
        "Success": True,
        "User": obj.get("User") or username,
        "Token": obj.get("Token") or token,
        "Score": int(obj.get("Score", 0) or 0),
        "SoftcoreScore": int(obj.get("SoftcoreScore", 0) or 0),
        "Messages": int(obj.get("Messages", 0) or 0),
    }
    _write_json(auth_path(), saved)
    return saved


def _parse_login_text(raw: str) -> tuple[str, str, str]:
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    kv = {}
    for ln in lines:
        if "=" in ln:
            k, v = ln.split("=", 1)
            kv[k.strip().upper()] = v.strip().strip('"').strip("'")
    if kv.get("USER") or kv.get("USERNAME"):
        user = kv.get("USER") or kv.get("USERNAME", "")
        return user, kv.get("PASSWORD", ""), kv.get("TOKEN", "")
    if len(lines) == 1 and ":" in lines[0]:
        user, secret = lines[0].split(":", 1)
        return user.strip(), secret.strip(), ""
    if len(lines) >= 2:
        return lines[0], lines[1], ""
    return "", "", ""


def import_login_file() -> dict | None:
    """Import credentials from ra_login.txt if present and scrub the plaintext file."""
    for path in login_import_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read()
        except OSError:
            continue
        user, pw, tok = _parse_login_text(raw)
        if not user or (not pw and not tok):
            continue
        saved = login(user, password=pw, token=tok)
        try:
            os.remove(path)
        except OSError:
            pass
        return saved
    return None


# --------------------------------------------------------------------------- #
# Award signature & queue flush
# --------------------------------------------------------------------------- #
def compute_award_signature(achievement_id: int, username: str, hardcore: int, offset_sec: int) -> str:
    """Matches rcheevos rc_api_init_award_achievement_request_hosted exact MD5."""
    aid_s = str(int(achievement_id))
    hc_s = "1" if hardcore else "0"
    if offset_sec > 0:
        msg = f"{aid_s}{username}{hc_s}{aid_s}{int(offset_sec)}"
    else:
        msg = f"{aid_s}{username}{hc_s}"
    return hashlib.md5(msg.encode("utf-8")).hexdigest()


def list_pending_awards() -> list[tuple[str, dict]]:
    qdir = queue_dir()
    if not os.path.isdir(qdir):
        return []
    out = []
    for name in sorted(os.listdir(qdir)):
        if not name.endswith(".json") or name.startswith(".tmp"):
            continue
        path = os.path.join(qdir, name)
        obj = _read_json(path)
        if isinstance(obj, dict) and int(obj.get("achievement_id", 0) or 0) > 0:
            out.append((path, obj))
    return out


def _record_history(item: dict, status: str, synced_utc: int) -> None:
    hist = _read_json(history_path(), default=[])
    if not isinstance(hist, list):
        hist = []
    hist.append({
        "achievement_id": int(item.get("achievement_id", 0)),
        "game_id": int(item.get("game_id", 0)),
        "title": str(item.get("title") or ""),
        "unlocked_at_utc": int(item.get("unlocked_at_utc", 0)),
        "synced_utc": int(synced_utc),
        "status": status,
    })
    _write_json(history_path(), hist[-MAX_HISTORY:])


def _is_already_awarded_error(err_msg: str) -> bool:
    low = (err_msg or "").lower()
    return "already has" in low or "already awarded" in low or "already unlocked" in low


def flush_queue(auth: dict | None = None, now_utc_override: int | None = None) -> dict:
    """Submit pending offline unlocks to RetroAchievements in chronological order."""
    auth = auth or get_auth()
    if not auth:
        return {"ok": False, "error": "Not logged in", "flushed": 0, "remaining": len(list_pending_awards())}

    pending = list_pending_awards()
    if not pending:
        return {"ok": True, "flushed": 0, "remaining": 0, "deduped": 0, "touched_gids": []}

    ref_utc = now_utc_override if now_utc_override is not None else (sync_utc_clock() or ra_clock.now_utc())
    user = auth["User"]
    token = auth["Token"]
    flushed = 0
    deduped = 0
    touched_gids: set[int] = set()
    last_error = ""

    for idx, (path, item) in enumerate(pending):
        aid = int(item.get("achievement_id", 0) or 0)
        gid = int(item.get("game_id", 0) or 0)
        game_hash = str(item.get("game_hash") or "")
        unlocked_at = int(item.get("unlocked_at_utc") or ref_utc)
        offset = max(0, min(MAX_BACKDATE_SEC, int(ref_utc - unlocked_at)))
        # Policy: offline backdated unlocks are always casual/softcore (h=0).
        hardcore = 0
        sig = compute_award_signature(aid, user, hardcore, offset)
        params = {
            "r": "awardachievement",
            "u": user,
            "t": token,
            "a": str(aid),
            "h": "0",
            "v": sig,
        }
        if game_hash:
            params["m"] = game_hash
        if offset > 0:
            params["o"] = str(offset)

        try:
            obj, _, server_utc = _http_dorequest(params)
            if server_utc:
                ref_utc = server_utc
        except ApiError as exc:
            last_error = str(exc)
            if exc.retryable:
                break
            # Non-retryable permanent rejection -> keep in queue only if unknown, else retire
            if _is_already_awarded_error(last_error):
                deduped += 1
                if gid > 0:
                    touched_gids.add(gid)
                _record_history(item, "already_unlocked", ref_utc)
                os.remove(path)
                continue
            break

        if obj.get("Success"):
            flushed += 1
            if gid > 0:
                touched_gids.add(gid)
            if "Score" in obj or "SoftcoreScore" in obj:
                auth["Score"] = int(obj.get("Score", auth.get("Score", 0)) or 0)
                auth["SoftcoreScore"] = int(obj.get("SoftcoreScore", auth.get("SoftcoreScore", 0)) or 0)
                _write_json(auth_path(), auth)
            _record_history(item, "awarded", ref_utc)
            os.remove(path)
        else:
            err = str(obj.get("Error") or "Award rejected")
            if _is_already_awarded_error(err):
                deduped += 1
                if gid > 0:
                    touched_gids.add(gid)
                _record_history(item, "already_unlocked", ref_utc)
                os.remove(path)
            else:
                last_error = err
                break

        if idx + 1 < len(pending) and SET_FETCH_DELAY_SEC > 0:
            time.sleep(min(0.25, SET_FETCH_DELAY_SEC))

    rem = len(list_pending_awards())
    return {
        "ok": rem == 0 or (flushed + deduped > 0 and not last_error),
        "flushed": flushed,
        "deduped": deduped,
        "remaining": rem,
        "touched_gids": sorted(touched_gids),
        "error": last_error,
    }


# --------------------------------------------------------------------------- #
# ROM scan & achievementsets / unlocks cache
# --------------------------------------------------------------------------- #
def iter_rom_files(root: str | None = None, folders: set[str] | list[str] | None = None) -> list[tuple[str, str]]:
    """Yield (rom_path, console_folder_name) across FunKey-OS ROM folders."""
    base = root or roms_root()
    wanted = set(folders) if folders is not None else None
    found: list[tuple[str, str]] = []
    for folder in ra_hash.CONSOLE_FOLDERS:
        if wanted is not None and folder not in wanted:
            continue
        folder_path = os.path.join(base, folder)
        if not os.path.isdir(folder_path):
            continue
        for dirpath, _, filenames in os.walk(folder_path):
            for fn in sorted(filenames):
                if fn.startswith("."):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                if ext in ra_hash.SKIP_EXTENSIONS:
                    continue
                if ext in ra_hash.ROM_EXTENSIONS:
                    found.append((os.path.join(dirpath, fn), folder))
    return found


def list_rom_folders(root: str | None = None) -> list[tuple[str, int]]:
    """Return non-empty (folder_name, rom_count) pairs under roms_root."""
    base = root or roms_root()
    out: list[tuple[str, int]] = []
    for folder in ra_hash.CONSOLE_FOLDERS:
        folder_path = os.path.join(base, folder)
        if not os.path.isdir(folder_path):
            continue
        count = 0
        for _, _, filenames in os.walk(folder_path):
            for fn in filenames:
                if fn.startswith("."):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                if ext not in ra_hash.SKIP_EXTENSIONS and ext in ra_hash.ROM_EXTENSIONS:
                    count += 1
        if count > 0:
            out.append((folder, count))
    return out


def _get_hash_library(console_id: int, cache: dict[int, dict[str, int] | None]) -> dict[str, int] | None:
    """Fetch {md5_lower: game_id} for console_id once per scan to skip unmatched ROMs."""
    if not console_id:
        return None
    if console_id in cache:
        return cache[console_id]
    try:
        obj, _, _ = _http_dorequest({
            "r": "hashlibrary",
            "c": str(int(console_id)),
        })
    except ApiError:
        cache[console_id] = None
        return None
    if not obj.get("Success") or not isinstance(obj.get("MD5List"), dict):
        cache[console_id] = None
        return None
    md5_map: dict[str, int] = {}
    for k, v in obj["MD5List"].items():
        try:
            md5_map[str(k).lower()] = int(v)
        except (TypeError, ValueError):
            pass
    cache[console_id] = md5_map
    return md5_map


def load_rom_hashes() -> dict:
    obj = _read_json(hashes_path(), default={})
    return obj if isinstance(obj, dict) else {}


def set_cache_path(ra_md5: str) -> str:
    return os.path.join(sets_dir(), f"m_{ra_md5.lower()}.json")


def unlocks_cache_path(game_id: int) -> str:
    return os.path.join(unlocks_dir(), f"g_{int(game_id)}.json")


def _extract_sets_summary(raw_obj: dict) -> tuple[int, str, int]:
    gid = int(raw_obj.get("GameId") or raw_obj.get("ID") or 0)
    title = str(raw_obj.get("Title") or "")
    cid = int(raw_obj.get("ConsoleId") or 0)
    return gid, title, cid


def _refresh_unlocks_for_games(
    gids: list[int] | set[int],
    auth: dict,
    progress_cb=None,
    cancel_cb=None,
) -> tuple[int, list[str], bool]:
    """Fetch and cache softcore unlock lists for the given sorted game IDs."""
    sorted_gids = sorted({int(g) for g in gids if int(g) > 0})
    if not sorted_gids:
        return 0, [], False
    user = auth["User"]
    token = auth["Token"]
    fetched = 0
    errors: list[str] = []
    cancelled = False
    for idx, gid in enumerate(sorted_gids, 1):
        if cancel_cb and cancel_cb():
            cancelled = True
            break
        if progress_cb:
            progress_cb("unlocks", idx, len(sorted_gids), f"Game #{gid}")
        uc_path = unlocks_cache_path(gid)
        try:
            obj, raw, _ = _http_dorequest({
                "r": "unlocks",
                "g": str(gid),
                "h": "0",
                "u": user,
                "t": token,
            })
            if obj.get("Success"):
                _write_atomic(uc_path, raw)
                fetched += 1
            if idx < len(sorted_gids) and SET_FETCH_DELAY_SEC > 0:
                time.sleep(min(0.2, SET_FETCH_DELAY_SEC))
        except ApiError as exc:
            errors.append(str(exc))
            if exc.retryable or exc.code in (401, 403):
                break
    return fetched, errors, cancelled


def scan_and_cache(
    auth: dict | None = None,
    refresh_unlocks: bool = True,
    progress_cb=None,
    folders: set[str] | list[str] | None = None,
    cancel_cb=None,
    force_unlocks: bool = False,
) -> dict:
    """Hash ROMs on /mnt, fetch missing achievementsets & unlocks, update rom_hashes.json."""
    auth = auth or get_auth()
    if not auth:
        raise ApiError("Not logged in")

    roms = iter_rom_files(folders=folders)
    index = load_rom_hashes()
    user = auth["User"]
    token = auth["Token"]

    hashed_new = 0
    sets_fetched = 0
    unlocks_fetched = 0
    matched_games: set[int] = set()
    newly_fetched_gids: set[int] = set()
    hashlib_cache: dict[int, dict[str, int] | None] = {}
    errors: list[str] = []
    total = len(roms)
    index_dirty = False
    dirty_ops = 0
    cancelled = False

    for idx, (path, folder) in enumerate(roms, 1):
        if cancel_cb and cancel_cb():
            cancelled = True
            break
        if progress_cb:
            progress_cb("scan", idx, total, os.path.basename(path))
        try:
            st = os.stat(path)
        except OSError:
            continue
        size = int(st.st_size)
        mtime = int(st.st_mtime)
        rec = index.get(path)
        if not (isinstance(rec, dict) and rec.get("size") == size and rec.get("mtime") == mtime and "hash" in rec):
            try:
                md5_hex, cid = ra_hash.hash_rom(path, folder_name=folder)
            except Exception:
                md5_hex, cid = None, ra_hash.infer_console_id(path, folder)
            if md5_hex is None:
                continue
            rec = {
                "size": size,
                "mtime": mtime,
                "hash": md5_hex,
                "console_id": cid or 0,
            }
            index[path] = rec
            hashed_new += 1
            index_dirty = True
            dirty_ops += 1

        md5_hex = rec["hash"]
        cid_hint = int(rec.get("console_id") or 0)
        sc_path = set_cache_path(md5_hex)
        gid = int(rec.get("game_id", -1))

        if not os.path.isfile(sc_path) and gid != 0:
            # Fast pre-filter using per-console hashlibrary (1 call per console vs 1 per ROM).
            hlib = _get_hash_library(cid_hint, hashlib_cache)
            if hlib is not None and md5_hex.lower() not in hlib:
                rec["game_id"] = 0
                gid = 0
                index_dirty = True
                dirty_ops += 1

        if not os.path.isfile(sc_path) and gid != 0:
            try:
                obj, raw, _ = _http_dorequest({
                    "r": "achievementsets",
                    "m": md5_hex,
                    "v": "2",
                    "u": user,
                    "t": token,
                })
                if obj.get("Success"):
                    gid, title, cid = _extract_sets_summary(obj)
                    rec["game_id"] = gid
                    if title:
                        rec["title"] = title
                    if cid:
                        rec["console_id"] = cid
                    index_dirty = True
                    dirty_ops += 1
                    _write_atomic(sc_path, raw)
                    sets_fetched += 1
                    if gid > 0:
                        newly_fetched_gids.add(gid)
                elif "unknown" in str(obj.get("Error") or "").lower():
                    rec["game_id"] = 0
                    index_dirty = True
                    dirty_ops += 1
                if SET_FETCH_DELAY_SEC > 0:
                    time.sleep(SET_FETCH_DELAY_SEC)
            except ApiError as exc:
                errors.append(str(exc))
                if exc.code in (401, 403):
                    break
                if exc.retryable:
                    # Network dropped or 429; persist partial progress and stop hitting upstream
                    break
        elif os.path.isfile(sc_path):
            if gid < 0:
                cached = _read_json(sc_path)
                if isinstance(cached, dict):
                    gid, title, cid = _extract_sets_summary(cached)
                    rec["game_id"] = gid
                    if title:
                        rec["title"] = title
                    if cid:
                        rec["console_id"] = cid
                    index_dirty = True
                    dirty_ops += 1

        if gid > 0:
            matched_games.add(gid)

        if index_dirty and dirty_ops >= 50:
            _write_json(hashes_path(), index)
            index_dirty = False
            dirty_ops = 0

    if index_dirty:
        _write_json(hashes_path(), index)

    if refresh_unlocks and matched_games and not errors and not cancelled:
        gids = [
            gid for gid in sorted(matched_games)
            if force_unlocks or gid in newly_fetched_gids or not os.path.isfile(unlocks_cache_path(gid))
        ]
        if gids:
            unlocks_fetched, unlock_errors, cancelled = _refresh_unlocks_for_games(
                gids, auth, progress_cb=progress_cb, cancel_cb=cancel_cb,
            )
            errors.extend(unlock_errors)

    return {
        "roms_found": total,
        "hashed_new": hashed_new,
        "sets_fetched": sets_fetched,
        "unlocks_fetched": unlocks_fetched,
        "matched_games": len(matched_games),
        "cancelled": cancelled,
        "error": errors[0] if errors else "",
    }


def sync_all(progress_cb=None, cancel_cb=None, include_scan: bool = False) -> dict:
    """Sync UTC clock -> flush pending queue -> refresh unlocks for flushed games (optionally scan ROMs)."""
    auth = get_auth() or import_login_file()
    if not auth:
        return {"ok": False, "error": "Not logged in"}

    if progress_cb:
        progress_cb("clock", 0, 1, "Syncing UTC clock...")
    utc = sync_utc_clock()

    if progress_cb:
        progress_cb("flush", 0, 1, "Submitting unlocks...")
    flush_res = flush_queue(auth=auth, now_utc_override=utc)
    if flush_res.get("error") and flush_res.get("remaining", 0) > 0:
        meta = {
            "last_sync_utc": utc or ra_clock.now_utc(),
            "last_error": flush_res["error"],
            "flushed": flush_res.get("flushed", 0),
        }
        _write_json(sync_meta_path(), meta)
        return {"ok": False, "flush": flush_res, "error": flush_res["error"]}

    unlocks_refreshed = 0
    refresh_errors: list[str] = []
    cancelled = False
    touched_gids = flush_res.get("touched_gids") or []
    if touched_gids and not include_scan:
        unlocks_refreshed, refresh_errors, cancelled = _refresh_unlocks_for_games(
            touched_gids, auth, progress_cb=progress_cb, cancel_cb=cancel_cb,
        )

    cache_res = None
    if include_scan and not cancelled:
        cache_res = scan_and_cache(
            auth=auth,
            refresh_unlocks=True,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
        )
        unlocks_refreshed += int(cache_res.get("unlocks_fetched", 0) or 0)
        if cache_res.get("error"):
            refresh_errors.append(cache_res["error"])
        cancelled = bool(cache_res.get("cancelled"))

    err_msg = refresh_errors[0] if refresh_errors else ""
    now_s = utc or ra_clock.now_utc()
    meta = {
        "last_sync_utc": now_s,
        "last_error": err_msg,
        "flushed": flush_res.get("flushed", 0),
    }
    if cache_res is not None:
        meta["matched_games"] = cache_res.get("matched_games", 0)
    _write_json(sync_meta_path(), meta)
    res = {
        "ok": not bool(err_msg) and not cancelled,
        "flush": flush_res,
        "unlocks_refreshed": unlocks_refreshed,
        "cancelled": cancelled,
        "error": err_msg,
    }
    if cache_res is not None:
        res["cache"] = cache_res
    return res


def game_titles_by_id() -> dict[int, str]:
    """Return {game_id: game_title} from rom_hashes.json."""
    out: dict[int, str] = {}
    for rec in load_rom_hashes().values():
        if not isinstance(rec, dict):
            continue
        gid = int(rec.get("game_id", 0) or 0)
        title = str(rec.get("title") or "").strip()
        if gid > 0 and title and gid not in out:
            out[gid] = title
    return out


def load_history(limit: int = 30) -> list[dict]:
    """Return synced unlock history entries, newest first."""
    hist = _read_json(history_path(), default=[])
    if not isinstance(hist, list):
        return []
    entries = [e for e in hist if isinstance(e, dict)]
    entries.reverse()
    return entries[:limit] if limit > 0 else entries


def status_summary() -> dict:
    auth = get_auth()
    pending = list_pending_awards()
    hashes = load_rom_hashes()
    cached_sets = 0
    if os.path.isdir(sets_dir()):
        cached_sets = sum(1 for n in os.listdir(sets_dir()) if n.startswith("m_") and n.endswith(".json"))
    matched = sum(1 for v in hashes.values() if isinstance(v, dict) and int(v.get("game_id", 0) or 0) > 0)
    meta = _read_json(sync_meta_path(), default={}) or {}
    clk = ra_clock.read_clock_sync()
    hist = _read_json(history_path(), default=[])
    hist_cnt = len(hist) if isinstance(hist, list) else 0
    return {
        "logged_in": auth is not None,
        "user": auth.get("User", "") if auth else "",
        "score": int(auth.get("Score", 0) or 0) if auth else 0,
        "score_casual": int(auth.get("SoftcoreScore", 0) or 0) if auth else 0,
        "pending_count": len(pending),
        "pending_items": [item for _, item in pending],
        "cached_sets": cached_sets,
        "roms_indexed": len(hashes),
        "roms_matched": matched,
        "history_count": hist_cnt,
        "last_sync_utc": int(meta.get("last_sync_utc", 0) or 0),
        "last_error": str(meta.get("last_error") or ""),
        "clock_synced": bool(clk.get("last_sync_utc")),
        "tz": ra_clock.read_configured_tz(),
    }


def hub_tag() -> str:
    """Short status string shown beside 'Achievements' in Connect hub."""
    pending = list_pending_awards()
    if pending:
        return f"{len(pending)} pending"
    if get_auth() is not None:
        return "ready"
    return "sign in"
