"""RetroAchievements Sync UI inside Connect.opk (for 240x240 and 240x160)."""

from datetime import datetime, timezone
import inspect
import textwrap
import time

import ra_backend
import ra_clock

MAX_COLS = 30
MAX_LINES = 6


def _wrap(lines: list[str], cols: int = MAX_COLS, max_lines: int = MAX_LINES) -> list[str]:
    out: list[str] = []
    for raw in lines:
        s = str(raw or "")
        if not s:
            out.append("")
            continue
        for w in textwrap.wrap(s, width=cols, break_long_words=True, break_on_hyphens=False):
            out.append(w)
    if len(out) > max_lines:
        out = out[:max_lines]
        out[-1] = (out[-1][: cols - 3] + "...") if len(out[-1]) >= cols else out[-1]
    return out


def _fmt_utc(ts: int) -> str:
    if not ts:
        return "never"
    tz = ra_clock.read_configured_tz()
    if tz:
        off = ra_clock.utc_offset_for_utc(tz, int(ts))
        return datetime.fromtimestamp(int(ts) + off, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _fmt_short(ts: int) -> str:
    if not ts:
        return "queued"
    tz = ra_clock.read_configured_tz()
    off = ra_clock.utc_offset_for_utc(tz, int(ts)) if tz else 0
    return datetime.fromtimestamp(int(ts) + off, tz=timezone.utc).strftime("%m-%d %H:%M")


def _call_busy(g, title: str, lines: list[str], hint: str = "", progress=None):
    sig = inspect.signature(g.busy)
    if "progress" in sig.parameters:
        return g.busy(title, lines, hint=hint, progress=progress)
    return g.busy(title, lines, hint=hint)


def _progress_drawer(g, title: str, cancellable: bool = False):
    state = {"phase": "", "ts": 0.0}

    def cb(phase: str, idx: int, total: int, label: str):
        now = time.monotonic()
        if phase == state["phase"] and idx < total and (now - state["ts"]) < 0.25:
            return
        state["phase"] = phase
        state["ts"] = now
        header = {
            "clock": "Syncing clock...",
            "flush": "Uploading unlocks...",
            "scan": "Scanning ROMs...",
            "unlocks": "Updating unlocks...",
        }.get(phase, "Syncing...")
        short_lbl = str(label or "")
        if len(short_lbl) > MAX_COLS:
            short_lbl = short_lbl[: MAX_COLS - 3] + "..."
        hint = "B cancel (saves progress)" if cancellable else ""
        prog = (idx, total) if phase in ("scan", "unlocks") and total > 0 else None
        _call_busy(g, title, [header, short_lbl], hint=hint, progress=prog)

    return cb


def _cancel_checker(g):
    state = {"cancelled": False}
    check = getattr(g, "check_cancel", None)
    if not check:
        return lambda: False

    def is_cancelled() -> bool:
        if not state["cancelled"] and check():
            state["cancelled"] = True
        return state["cancelled"]

    return is_cancelled


def _do_import_login(g):
    g.busy("Achievements", ["Checking ra_login.txt..."])
    try:
        imported = ra_backend.import_login_file()
    except ra_backend.ApiError as exc:
        g.message("Sign In Failed", _wrap([str(exc)]))
        return None
    if imported:
        g.message("Signed In", _wrap([
            f"User: {imported.get('User', '')}",
            "ra_login.txt scrubbed.",
        ]))
        return imported
    g.message("Import Login", _wrap([
        "No ra_login.txt found.",
        "Put user:pass in",
        "/mnt/FunKey/raoffline/",
        "login.txt or /mnt/.",
    ]))
    return None


def _account_menu(g):
    while True:
        auth = ra_backend.get_auth()
        if auth:
            user = auth.get("User", "")
            items = [
                ("Sign Out", user[:12]),
                ("Import login.txt", ""),
                ("Back", ""),
            ]
            c = g.menu("RA Account", items)
            if c in (None, 2):
                return None
            if c == "QUIT":
                return "QUIT"
            if c == 0:
                if g.confirm("Sign Out", _wrap([f"Sign out {user}?", "Cache & queue kept."])):
                    ra_backend.logout()
            elif c == 1:
                _do_import_login(g)
        else:
            items = [
                ("Sign In", ""),
                ("Import login.txt", ""),
                ("Back", ""),
            ]
            c = g.menu("RA Account", items)
            if c in (None, 2):
                return None
            if c == "QUIT":
                return "QUIT"
            if c == 0:
                user = g.keyboard("RA Username")
                if not user:
                    continue
                pw = g.keyboard("RA Password")
                if not pw:
                    continue
                g.busy("RA Sign In", ["Connecting...", user[:MAX_COLS]])
                try:
                    saved = ra_backend.login(user, password=pw)
                except ra_backend.ApiError as exc:
                    g.message("Sign In Failed", _wrap([str(exc)]))
                    continue
                g.message("Signed In", _wrap([
                    f"User: {saved.get('User', '')}",
                    f"Score: {saved.get('Score', 0)}",
                    f"Casual: {saved.get('SoftcoreScore', 0)}",
                ]))
            elif c == 1:
                _do_import_login(g)


def _pending_menu(g):
    titles = ra_backend.game_titles_by_id()
    while True:
        pending = ra_backend.list_pending_awards()
        if not pending:
            g.message("Pending Queue", _wrap([
                "Queue is empty.",
                "",
                "Softcore unlocks earned",
                "offline queue here.",
            ]))
            return
        items = []
        for _, item in pending:
            title = str(item.get("title") or f"Ach #{item.get('achievement_id', 0)}")
            if len(title) > 16:
                title = title[:15] + "."
            ts = int(item.get("unlocked_at_utc", 0) or 0)
            items.append((title, _fmt_short(ts)))
        items.append(("Back", ""))
        c = g.menu(f"Pending ({len(pending)})", items)
        if c in (None, len(pending)):
            return
        if c == "QUIT":
            return "QUIT"
        _, item = pending[c]
        gid = int(item.get("game_id", 0) or 0)
        game_name = titles.get(gid) or f"Game #{gid}"
        g.message("Queued Unlock", _wrap([
            str(item.get("title") or f"Achievement #{item.get('achievement_id', 0)}"),
            game_name,
            f"Ach ID: {item.get('achievement_id', 0)}",
            f"When: {_fmt_utc(item.get('unlocked_at_utc', 0))}",
        ]))


def _history_menu(g):
    titles = ra_backend.game_titles_by_id()
    hist = ra_backend.load_history()
    if not hist:
        g.message("Sync History", _wrap([
            "No synced unlocks yet.",
            "",
            "Uploaded achievements",
            "appear here.",
        ]))
        return None
    sel = 0
    while True:
        items = []
        for entry in hist:
            title = str(entry.get("title") or f"Ach #{entry.get('achievement_id', 0)}")
            tag = "dup" if entry.get("status") == "already_unlocked" else _fmt_short(int(entry.get("synced_utc", 0) or 0))
            items.append((title, tag))
        items.append(("Back", ""))
        c = g.menu("History", items, status=f"{len(hist)} synced", sel=sel)
        if c in (None, len(hist)):
            return None
        if c == "QUIT":
            return "QUIT"
        sel = c
        entry = hist[c]
        gid = int(entry.get("game_id", 0) or 0)
        game_name = titles.get(gid) or f"Game #{gid}"
        st_lbl = "Already unlocked" if entry.get("status") == "already_unlocked" else "Uploaded"
        g.message("Synced Unlock", _wrap([
            str(entry.get("title") or f"Achievement #{entry.get('achievement_id', 0)}"),
            game_name,
            f"Status: {st_lbl}",
            f"Earned: {_fmt_utc(entry.get('unlocked_at_utc', 0))}",
            f"Synced: {_fmt_utc(entry.get('synced_utc', 0))}",
        ]))


def _can_usb_reconnect() -> bool:
    try:
        import usb_backend
        st = usb_backend.state()
        return st.get("mode") == "adb" and not st.get("host")
    except Exception:
        return False


def _do_sync(g):
    def _run_once():
        g.busy("Achievements", ["Starting sync..."], hint="B cancel (saves progress)")
        return ra_backend.sync_all(
            progress_cb=_progress_drawer(g, "RA Sync", cancellable=True),
            cancel_cb=_cancel_checker(g),
        )

    res = _run_once()
    if not res.get("ok") and res.get("error") == "Not logged in":
        g.message("Achievements", _wrap([
            "Not signed in.",
            "Open Account -> Sign In",
            "or drop login.txt.",
        ]))
        return
    err_low = str(res.get("error") or "").lower()
    if (not res.get("ok") and not res.get("cancelled")
            and any(k in err_low for k in ("dhcp", "internet", "dns", "timed out"))
            and _can_usb_reconnect()):
        if g.confirm("Sync Failed", _wrap([res["error"], "", "Reconnect USB and retry?"])):
            import usb_backend
            g.busy("USB", ["Reconnecting USB...", "(up to ~30s)"])
            usb_backend.reconnect()
            res = _run_once()
    fl = res.get("flush") or {}
    ca = res.get("cache") or {}
    lines = [
        f"Uploaded: {fl.get('flushed', 0)}  Dup: {fl.get('deduped', 0)}",
        f"Left queued: {fl.get('remaining', 0)}",
    ]
    if res.get("unlocks_refreshed") and not ca:
        lines.append(f"Games refreshed: {res['unlocks_refreshed']}")
    if ca:
        lines.append(f"ROMs: {ca.get('roms_found', 0)}  Games: {ca.get('matched_games', 0)}")
        lines.append(f"New sets: {ca.get('sets_fetched', 0)}  Unl: {ca.get('unlocks_fetched', 0)}")
    if res.get("error"):
        lines.append(f"Err: {res['error']}")
    if res.get("cancelled"):
        title = "Sync Cancelled"
    elif res.get("ok"):
        title = "Sync Complete"
    else:
        title = "Sync Partial"
    g.message(title, _wrap(lines))


def _do_scan(g):
    if not ra_backend.get_auth():
        g.message("Scan ROMs", _wrap(["Sign in first so sets", "can be downloaded."]))
        return
    g.busy("Scan ROMs", ["Counting ROM folders..."])
    folders = ra_backend.list_rom_folders()
    if not folders:
        g.message("Scan ROMs", _wrap(["No ROM folders found", f"under {ra_backend.roms_root()}."]))
        return
    total_all = sum(cnt for _, cnt in folders)
    items = [("All Systems", str(total_all))]
    for fname, cnt in folders:
        items.append((fname, str(cnt)))
    items.append(("Back", ""))
    c = g.menu("Scan System", items)
    if c in (None, len(items) - 1):
        return None
    if c == "QUIT":
        return "QUIT"
    chosen = None if c == 0 else [folders[c - 1][0]]
    g.busy("Scan ROMs", ["Starting scan..."], hint="B cancel (saves progress)")
    try:
        res = ra_backend.scan_and_cache(
            progress_cb=_progress_drawer(g, "Scan ROMs", cancellable=True),
            folders=chosen,
            cancel_cb=_cancel_checker(g),
        )
    except ra_backend.ApiError as exc:
        g.message("Scan Failed", _wrap([str(exc)]))
        return
    lines = [
        f"ROMs: {res.get('roms_found', 0)}  New hash: {res.get('hashed_new', 0)}",
        f"Matched games: {res.get('matched_games', 0)}",
        f"Sets+: {res.get('sets_fetched', 0)}  Unlocks+: {res.get('unlocks_fetched', 0)}",
    ]
    if res.get("error"):
        lines.append(f"Err: {res['error']}")
    title = "Scan Cancelled" if res.get("cancelled") else "Scan Done"
    g.message(title, _wrap(lines))


def _show_status(g):
    st = ra_backend.status_summary()
    lines = [
        f"User: {st['user']}" if st["logged_in"] else "User: not signed in",
        f"Points: {st['score']} (soft {st['score_casual']})",
        f"Pending: {st['pending_count']}  Sets: {st['cached_sets']}",
        f"ROMs: {st['roms_matched']}/{st['roms_indexed']} matched",
        f"Clock: {'synced' if st['clock_synced'] else 'no sync'} ({st['tz'] or 'no TZ'})",
    ]
    if st.get("last_error"):
        lines.append(f"Err: {st['last_error']}")
    g.message("RA Status", _wrap(lines))


def ra_menu(g):
    sel = 0
    while True:
        st = ra_backend.status_summary()
        pend = st["pending_count"]
        hcnt = st.get("history_count", 0)
        items = [
            ("Sync Now", f"{pend} up" if pend else "run"),
            ("Pending Queue", str(pend)),
            ("Scan ROMs", f"{st['cached_sets']} sets"),
            ("Account", st["user"][:10] if st["logged_in"] else "sign in"),
            ("Status", ""),
            ("History", str(hcnt) if hcnt else ""),
            ("Back", ""),
        ]
        c = g.menu("Achievements", items, sel=sel)
        if c in (None, len(items) - 1):
            return None
        if c == "QUIT":
            return "QUIT"
        sel = c
        res = None
        if c == 0:
            _do_sync(g)
        elif c == 1:
            res = _pending_menu(g)
        elif c == 2:
            res = _do_scan(g)
        elif c == 3:
            res = _account_menu(g)
        elif c == 4:
            _show_status(g)
        elif c == 5:
            res = _history_menu(g)
        if res == "QUIT":
            return "QUIT"
