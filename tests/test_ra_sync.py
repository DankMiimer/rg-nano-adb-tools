#!/usr/bin/env python3
"""Offline tests for the RetroAchievements sync modules in connect/app/.

Covers:
  - ra_clock: POSIX TZ evaluation, DST transitions, clock_sync.json persistence
  - ra_hash: console ID inference, header stripping (NES/FDS/SNES/PCE/Lynx), ZIP streaming
  - ra_backend: auth, plaintext login scrub, queue flush (h=0, o=, MD5 v=, 429, dedup),
                incremental ROM scanning & achievementsets/unlocks caching
  - ra_gui: menus & messages on both 240x240 (RG Nano) and 240x160 (GBA Mini)
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import http.server
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.parse
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "wifi", "app"))
sys.path.insert(0, os.path.join(HERE, "..", "connect", "app"))

T = tempfile.mkdtemp(prefix="ra_sync_test_")
os.environ.update({
    "NANO_FB_HEADLESS": "1",
    "NANO_FKGPIOD": os.path.join(T, "fkgpiod.conf"),
    "RA_DATA": os.path.join(T, "raoffline"),
    "RA_ROMS": os.path.join(T, "roms"),
    "RA_FETCH_DELAY": "0",
    "WIFI_CONF": os.path.join(T, "wifi.conf"),
})

import wifi_gui  # noqa: E402
import ra_clock  # noqa: E402
import ra_hash  # noqa: E402
import ra_backend  # noqa: E402
import ra_gui  # noqa: E402


def _utc(y, m, d, hh=0, mm=0, ss=0) -> int:
    return int(datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc).timestamp())


class FakeKeys:
    queue: list[str] = []

    def get(self):
        if not self.queue:
            raise AssertionError("Scripted button queue exhausted")
        return self.queue.pop(0)

    def wait_any(self):
        return self.get()


class RecorderGui(wifi_gui.Gui):
    def __init__(self):
        super().__init__()
        self.btns = FakeKeys()
        self.seen: list[tuple] = []

    def message(self, title, lines, hint="A/B back", wait=True):
        self.seen.append(("message", title, list(lines)))
        return super().message(title, lines, hint, wait)

    def confirm(self, title, lines):
        self.seen.append(("confirm", title, list(lines)))
        return super().confirm(title, lines)

    def menu(self, title, items, status="", show_full=False, sel=0):
        self.seen.append(("menu", title, list(items), sel))
        return super().menu(title, items, status, show_full, sel)

    def busy(self, title, lines, hint="", progress=None):
        self.seen.append(("busy", title, list(lines)))
        return super().busy(title, lines, hint=hint, progress=progress)


class MockRAHandler(http.server.BaseHTTPRequestHandler):
    server_version = "MockRA/1.0"

    def log_message(self, format, *args):
        pass

    def _send_json(self, code: int, obj: dict, date_str: str = "Sat, 14 Nov 2026 12:00:00 GMT"):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Date", date_str)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.server.calls.append(("GET", self.path, {}))
        self._send_json(200, {"Success": True}, date_str=self.server.date_hdr)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        params = {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}
        self.server.calls.append(("POST", self.headers.get("User-Agent", ""), params))
        r = params.get("r", "")

        if r == "login2":
            if params.get("u") == "TestUser" and params.get("p") == "SecretPass!":
                self._send_json(200, {
                    "Success": True,
                    "User": "TestUser",
                    "Token": "TOK_ABC_123",
                    "Score": 1200,
                    "SoftcoreScore": 350,
                    "Messages": 0,
                }, date_str=self.server.date_hdr)
            else:
                self._send_json(200, {"Success": False, "Error": "Invalid credentials"}, date_str=self.server.date_hdr)
            return

        if r == "awardachievement":
            aid = int(params.get("a", "0") or 0)
            override = self.server.award_overrides.get(aid)
            if override:
                code, payload = override
                self._send_json(code, payload, date_str=self.server.date_hdr)
                return
            self.server.awarded_score += 25
            self._send_json(200, {
                "Success": True,
                "AchievementID": aid,
                "Score": 1200,
                "SoftcoreScore": self.server.awarded_score,
            }, date_str=self.server.date_hdr)
            return

        if r == "achievementsets":
            md5_hex = params.get("m", "")
            if md5_hex in self.server.sets_by_hash:
                self._send_json(200, self.server.sets_by_hash[md5_hex], date_str=self.server.date_hdr)
            else:
                self._send_json(200, {"Success": False, "Error": "Unknown game"}, date_str=self.server.date_hdr)
            return

        if r == "unlocks":
            gid = int(params.get("g", "0") or 0)
            self._send_json(200, {
                "Success": True,
                "GameID": gid,
                "UserUnlocks": self.server.unlocks_by_game.get(gid, []),
            }, date_str=self.server.date_hdr)
            return

        if r == "hashlibrary":
            cid = int(params.get("c", "0") or 0)
            if not self.server.enable_hashlibrary:
                self._send_json(404, {"Success": False}, date_str=self.server.date_hdr)
                return
            md5s = {
                h: int(s.get("GameId", 0))
                for h, s in self.server.sets_by_hash.items()
                if int(s.get("ConsoleId", 0)) == cid and int(s.get("GameId", 0)) > 0
            }
            self._send_json(200, {"Success": True, "MD5List": md5s}, date_str=self.server.date_hdr)
            return

        self._send_json(400, {"Success": False, "Error": "Bad request"})


def start_mock_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockRAHandler)
    srv.calls = []
    srv.date_hdr = "Sat, 14 Nov 2026 12:00:00 GMT"
    srv.award_overrides = {}
    srv.awarded_score = 350
    srv.sets_by_hash = {}
    srv.unlocks_by_game = {}
    srv.enable_hashlibrary = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


class ClockTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(T, ignore_errors=True)
        os.makedirs(T, exist_ok=True)

    def test_fixed_offsets(self):
        self.assertEqual(ra_clock.utc_offset_for_utc("UTC0", _utc(2026, 6, 1)), 0)
        self.assertEqual(ra_clock.utc_offset_for_utc("EST5", _utc(2026, 1, 15)), -18000)
        self.assertEqual(ra_clock.utc_offset_for_utc("IST-5:30", _utc(2026, 1, 15)), 19800)

    def test_cet_cest_dst_transition(self):
        tz = "CET-1CEST,M3.5.0,M10.5.0/3"
        # 2026 last Sunday of March is Mar 29; at 01:00 UTC (02:00 CET) clocks jump to 03:00 CEST
        before_spring = _utc(2026, 3, 29, 0, 59, 0)
        after_spring = _utc(2026, 3, 29, 1, 0, 0)
        self.assertEqual(ra_clock.utc_offset_for_utc(tz, before_spring), 3600)
        self.assertEqual(ra_clock.utc_offset_for_utc(tz, after_spring), 7200)
        # Roundtrip local_epoch_to_utc
        self.assertEqual(ra_clock.local_epoch_to_utc(tz, after_spring + 7200), after_spring)
        # Last Sunday of Oct 2026 is Oct 25; 03:00 CEST == 01:00 UTC
        self.assertEqual(ra_clock.utc_offset_for_utc(tz, _utc(2026, 10, 25, 0, 30, 0)), 7200)
        self.assertEqual(ra_clock.utc_offset_for_utc(tz, _utc(2026, 10, 25, 1, 30, 0)), 3600)

    def test_southern_hemisphere_and_julian(self):
        au_tz = "AEST-10AEDT,M10.1.0,M4.1.0/3"
        # Jan is summer DST (+11h), July is winter (+10h)
        self.assertEqual(ra_clock.utc_offset_for_utc(au_tz, _utc(2026, 1, 15, 0)), 11 * 3600)
        self.assertEqual(ra_clock.utc_offset_for_utc(au_tz, _utc(2026, 7, 15, 0)), 10 * 3600)
        # J60 = March 1 even in leap years (2024 is leap)
        j_tz = "TEST-1TDST,J60,J300"
        self.assertEqual(ra_clock.utc_offset_for_utc(j_tz, _utc(2024, 2, 29, 12)), 3600)
        self.assertEqual(ra_clock.utc_offset_for_utc(j_tz, _utc(2024, 3, 1, 12)), 7200)

    def test_clock_sync_persistence_and_now_utc(self):
        with open(os.environ["WIFI_CONF"], "w", encoding="utf-8") as f:
            f.write('SSID="test"\nTZ="CET-1CEST,M3.5.0,M10.5.0/3"\n')
        utc_ref = _utc(2026, 7, 10, 14, 0, 0)  # July -> +7200 CEST
        saved = ra_clock.write_clock_sync(utc_ref)
        self.assertEqual(saved["utc_offset_sec"], 7200)
        self.assertEqual(ra_clock.now_utc(utc_ref + 7200), utc_ref)

        # Fallback to stored utc_offset_sec if WIFI_CONF disappears
        os.remove(os.environ["WIFI_CONF"])
        self.assertEqual(ra_clock.now_utc(utc_ref + 7200), utc_ref)


class HashTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(T, ignore_errors=True)
        os.makedirs(T, exist_ok=True)

    def _write(self, rel: str, data: bytes) -> str:
        p = os.path.join(T, "roms", rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_nes_and_fds_strip_16_bytes(self):
        body = b"PRG" * 100
        nes = self._write("NES/test.nes", b"NES\x1a" + b"\x00" * 12 + body)
        self.assertEqual(ra_hash.hash_rom(nes, folder_name="NES"), (hashlib.md5(body).hexdigest(), 7))
        fds = self._write("NES/disk.fds", b"FDS\x1a" + b"\x01" * 12 + body)
        self.assertEqual(ra_hash.hash_rom(fds, folder_name="NES"), (hashlib.md5(body).hexdigest(), 81))

    def test_snes_copier_header_strip(self):
        body = b"S" * 0x4000
        raw_sfc = self._write("SNES/raw.sfc", body)
        copier_smc = self._write("SNES/hdr.smc", b"\xaa" * 512 + body)
        exp = hashlib.md5(body).hexdigest()
        self.assertEqual(ra_hash.hash_rom(raw_sfc, folder_name="SNES"), (exp, 3))
        self.assertEqual(ra_hash.hash_rom(copier_smc, folder_name="SNES"), (exp, 3))

    def test_pce_and_lynx_header_strip(self):
        pce_body = b"P" * 1024
        pce = self._write("PCE-TurboGrafx/game.pce", b"\x55" * 512 + pce_body)
        self.assertEqual(ra_hash.hash_rom(pce, folder_name="PCE-TurboGrafx"), (hashlib.md5(pce_body).hexdigest(), 8))

        lynx_body = b"L" * 2048
        lynx = self._write("Atari lynx/game.lnx", b"LYNX\x00" + b"\x00" * 59 + lynx_body)
        self.assertEqual(ra_hash.hash_rom(lynx, folder_name="Atari lynx"), (hashlib.md5(lynx_body).hexdigest(), 13))

    def test_zip_streaming_and_disc_skip(self):
        gba_data = b"GBA!" * 1024
        zp = os.path.join(T, "roms", "Game Boy Advance", "game.zip")
        os.makedirs(os.path.dirname(zp), exist_ok=True)
        with zipfile.ZipFile(zp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("readme.txt", b"ignore me")
            zf.writestr("SuperGame.gba", gba_data)
        self.assertEqual(
            ra_hash.hash_rom(zp, folder_name="Game Boy Advance"),
            (hashlib.md5(gba_data).hexdigest(), 5),
        )
        chd = self._write("PS1/game.chd", b"MComprHD")
        self.assertEqual(ra_hash.hash_rom(chd, folder_name="PS1"), (None, 12))


class BackendTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(T, ignore_errors=True)
        os.makedirs(os.path.join(T, "roms", "Game Boy Advance"), exist_ok=True)
        self.srv = start_mock_server()
        os.environ["RA_HOST"] = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_login_and_import_scrub(self):
        with self.assertRaises(ra_backend.ApiError):
            ra_backend.login("TestUser", password="wrong")
        auth = ra_backend.login("TestUser", password="SecretPass!")
        self.assertEqual(auth["Token"], "TOK_ABC_123")
        with open(ra_backend.auth_path(), "r", encoding="utf-8") as f:
            self.assertNotIn("SecretPass!", f.read())
        self.assertEqual(ra_backend.hub_tag(), "ready")

        ra_backend.logout()
        imp_path = os.path.join(os.environ["RA_ROMS"], "ra_login.txt")
        with open(imp_path, "w", encoding="utf-8") as f:
            f.write("TestUser:SecretPass!\n")
        imported = ra_backend.import_login_file()
        self.assertEqual(imported["User"], "TestUser")
        self.assertFalse(os.path.exists(imp_path))

    def test_flush_queue_backdate_signature_dedup_and_retry(self):
        ra_backend.login("TestUser", password="SecretPass!")
        qdir = ra_backend.queue_dir()
        os.makedirs(qdir, exist_ok=True)
        ref_utc = _utc(2026, 11, 14, 12, 0, 0)

        # Item 1: 30 minutes ago (within 14d -> o=1800)
        with open(os.path.join(qdir, "01.json"), "w", encoding="utf-8") as f:
            json.dump({
                "achievement_id": 101, "game_id": 12, "game_hash": "deadbeef" * 4,
                "hardcore": 1, "unlocked_at_utc": ref_utc - 1800, "title": "First",
            }, f)
        # Item 2: already unlocked dedup (40 days ago -> clamped to 14*86400)
        with open(os.path.join(qdir, "02.json"), "w", encoding="utf-8") as f:
            json.dump({
                "achievement_id": 102, "game_id": 12, "game_hash": "deadbeef" * 4,
                "hardcore": 0, "unlocked_at_utc": ref_utc - 40 * 86400, "title": "Dup",
            }, f)
        # Item 3: hits 429 -> must remain queued
        with open(os.path.join(qdir, "03.json"), "w", encoding="utf-8") as f:
            json.dump({
                "achievement_id": 103, "game_id": 12, "game_hash": "deadbeef" * 4,
                "hardcore": 0, "unlocked_at_utc": ref_utc - 60, "title": "RateLimited",
            }, f)

        self.srv.award_overrides[102] = (200, {"Success": False, "Error": "User already has this achievement unlocked."})
        self.srv.award_overrides[103] = (429, {"Success": False, "Error": "Too Many Requests"})

        self.assertEqual(ra_backend.hub_tag(), "3 pending")
        res = ra_backend.flush_queue(now_utc_override=ref_utc)
        self.assertEqual(res["flushed"], 1)
        self.assertEqual(res["deduped"], 1)
        self.assertEqual(res["remaining"], 1)

        awards = [p for _, ua, p in self.srv.calls if p.get("r") == "awardachievement"]
        self.assertEqual(len(awards), 3)
        # Verify softcore forced + offset + exact MD5 v=
        self.assertEqual(awards[0]["h"], "0")
        self.assertEqual(awards[0]["o"], "1800")
        self.assertEqual(awards[0]["v"], hashlib.md5(b"101TestUser01011800").hexdigest())
        self.assertEqual(awards[1]["o"], str(14 * 86400))
        self.assertTrue(os.path.isfile(os.path.join(qdir, "03.json")))
        self.assertFalse(os.path.isfile(os.path.join(qdir, "01.json")))
        self.assertFalse(os.path.isfile(os.path.join(qdir, "02.json")))
        # User-Agent should be our custom string, never Python-urllib
        for _, ua, p in self.srv.calls:
            if p:
                self.assertIn("RG-Nano-Connect/", ua)

    def test_incremental_scan_and_sync_all(self):
        ra_backend.login("TestUser", password="SecretPass!")
        rom_bytes = b"TESTGBA" * 256
        rom_path = os.path.join(os.environ["RA_ROMS"], "Game Boy Advance", "Metroid.gba")
        with open(rom_path, "wb") as f:
            f.write(rom_bytes)
        md5_hex = hashlib.md5(rom_bytes).hexdigest()

        self.srv.sets_by_hash[md5_hex] = {
            "Success": True,
            "GameId": 777,
            "Title": "Metroid Fusion",
            "ConsoleId": 5,
            "Sets": [],
        }
        self.srv.unlocks_by_game[777] = [10, 11]

        res1 = ra_backend.scan_and_cache()
        self.assertEqual(res1["roms_found"], 1)
        self.assertEqual(res1["hashed_new"], 1)
        self.assertEqual(res1["sets_fetched"], 1)
        self.assertEqual(res1["unlocks_fetched"], 1)
        self.assertTrue(os.path.isfile(ra_backend.set_cache_path(md5_hex)))
        self.assertTrue(os.path.isfile(ra_backend.unlocks_cache_path(777)))

        # Second scan: already-cached sets & unlocks are skipped (0 network calls)
        self.srv.calls.clear()
        res2 = ra_backend.scan_and_cache()
        self.assertEqual(res2["hashed_new"], 0)
        self.assertEqual(res2["sets_fetched"], 0)
        self.assertEqual(res2["unlocks_fetched"], 0)
        self.assertEqual(self.srv.calls, [])

        # Queue 2 unlocks for game 12: Sync Now must upload them and refresh ONLY game 12
        # (never scanning ROMs or fetching unlocks for unrelated cached game 777).
        qdir = ra_backend.queue_dir()
        os.makedirs(qdir, exist_ok=True)
        for aid in (201, 202):
            with open(os.path.join(qdir, f"{aid}.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "achievement_id": aid, "game_id": 12, "game_hash": "deadbeef" * 4,
                    "hardcore": 0, "unlocked_at_utc": _utc(2026, 11, 14, 11, 55, 0),
                }, f)
        self.srv.unlocks_by_game[12] = [201, 202]
        self.srv.calls.clear()
        sync_res = ra_backend.sync_all()
        self.assertTrue(sync_res["ok"])
        self.assertEqual(sync_res["flush"]["flushed"], 2)
        self.assertEqual(sync_res["unlocks_refreshed"], 1)
        self.assertNotIn("cache", sync_res)
        req_types = [p.get("r") for m, _, p in self.srv.calls if m == "POST"]
        self.assertEqual(req_types, ["awardachievement", "awardachievement", "unlocks"])
        unlock_gids = [p.get("g") for _, _, p in self.srv.calls if p.get("r") == "unlocks"]
        self.assertEqual(unlock_gids, ["12"])
        self.assertTrue(os.path.isfile(ra_backend.unlocks_cache_path(12)))

    def test_hashlibrary_skips_unmatched_and_cancel_saves_progress(self):
        ra_backend.login("TestUser", password="SecretPass!")
        gba_dir = os.path.join(os.environ["RA_ROMS"], "Game Boy Advance")
        nes_dir = os.path.join(os.environ["RA_ROMS"], "NES")
        os.makedirs(nes_dir, exist_ok=True)
        for i in range(4):
            with open(os.path.join(gba_dir, f"Hack{i}.gba"), "wb") as f:
                f.write(f"HACK{i}".encode() * 64)
        with open(os.path.join(nes_dir, "SMB.nes"), "wb") as f:
            f.write(b"NES\x1a" + b"\x00" * 12 + b"SMB!" * 64)

        known_bytes = b"KNOWN_GBA" * 64
        with open(os.path.join(gba_dir, "Known.gba"), "wb") as f:
            f.write(known_bytes)
        known_md5 = hashlib.md5(known_bytes).hexdigest()
        self.srv.sets_by_hash[known_md5] = {
            "Success": True, "GameId": 901, "Title": "Known GBA", "ConsoleId": 5, "Sets": [],
        }

        # Filter to GBA only: ignores NES folder and hashlibrary skips 4 hacks (only 1 achievementsets call!)
        folders = ra_backend.list_rom_folders()
        self.assertIn(("Game Boy Advance", 5), folders)
        self.assertIn(("NES", 1), folders)

        self.srv.calls.clear()
        res = ra_backend.scan_and_cache(folders=["Game Boy Advance"], refresh_unlocks=False)
        self.assertEqual(res["roms_found"], 5)
        self.assertEqual(res["matched_games"], 1)
        self.assertFalse(res["cancelled"])
        ach_calls = [p for _, _, p in self.srv.calls if p.get("r") == "achievementsets"]
        self.assertEqual(len(ach_calls), 1)
        self.assertEqual(ach_calls[0]["m"], known_md5)

        # Cancel mid-scan right after the 1st NES ROM (GBA 5 already cached -> 0 new, NES -> +1, then stop before unlock pass)
        hits = {"n": 0}
        def stop_after_nes():
            hits["n"] += 1
            return hits["n"] > 6
        res_cancel = ra_backend.scan_and_cache(folders=["NES", "Game Boy Advance"], cancel_cb=stop_after_nes)
        self.assertTrue(res_cancel["cancelled"])
        self.assertEqual(res_cancel["hashed_new"], 1)
        self.assertTrue(os.path.isfile(ra_backend.hashes_path()))

    def test_network_error_formatting_for_usb_dhcp_and_dns(self):
        import socket
        import urllib.error
        from unittest import mock

        ra_backend.login("TestUser", password="SecretPass!")
        qdir = ra_backend.queue_dir()
        os.makedirs(qdir, exist_ok=True)
        with open(os.path.join(qdir, "01.json"), "w", encoding="utf-8") as f:
            json.dump({"achievement_id": 101, "game_id": 1, "unlocked_at_utc": _utc(2026, 11, 14, 10, 0)}, f)

        run_dir = os.path.join(T, "usblink_run")
        os.makedirs(run_dir, exist_ok=True)
        err = urllib.error.URLError(socket.gaierror(-3, "Try again"))
        with mock.patch.dict(os.environ, {"USBLINK_RUN": run_dir}):
            with mock.patch("urllib.request.urlopen", side_effect=err):
                res = ra_backend.sync_all()
            self.assertFalse(res["ok"])
            self.assertIn("No internet/DNS", res["error"])

            flag = os.path.join(run_dir, "usblink-dhcp-pending")
            with open(flag, "w", encoding="utf-8") as f:
                f.write("")
            with mock.patch("urllib.request.urlopen", side_effect=err):
                res2 = ra_backend.sync_all()
            self.assertFalse(res2["ok"])
            self.assertIn("USB DHCP failed", res2["error"])


class GuiTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(T, ignore_errors=True)
        os.makedirs(os.path.join(T, "roms", "Game Boy Advance"), exist_ok=True)
        self.srv = start_mock_server()
        os.environ["RA_HOST"] = f"http://127.0.0.1:{self.srv.server_address[1]}"
        ra_backend.login("TestUser", password="SecretPass!")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def _run_ra(self, keys: list[str], gba_mini: bool = False):
        if gba_mini:
            with open(os.environ["NANO_FKGPIOD"], "w", encoding="utf-8") as f:
                f.write("MAP START+A TO KEY KEY_X\n")
        elif os.path.exists(os.environ["NANO_FKGPIOD"]):
            os.remove(os.environ["NANO_FKGPIOD"])

        FakeKeys.queue = list(keys)
        old = wifi_gui.Buttons
        wifi_gui.Buttons = FakeKeys
        g = RecorderGui()
        try:
            ra_gui.ra_menu(g)
        finally:
            wifi_gui.Buttons = old
            g.close()
        self.assertEqual(FakeKeys.queue, [])
        return g

    def test_ra_menus_fit_rg_nano_and_gba_mini(self):
        # Write 1 queued unlock so Pending Queue renders an item + detail message
        qdir = ra_backend.queue_dir()
        os.makedirs(qdir, exist_ok=True)
        with open(os.path.join(qdir, "01.json"), "w", encoding="utf-8") as f:
            json.dump({
                "achievement_id": 500, "game_id": 42,
                "unlocked_at_utc": _utc(2026, 11, 14, 10, 0, 0),
                "title": "A Very Long Achievement Name For Screen Width Testing",
            }, f)

        # Add 1 GBA ROM so Scan ROMs opens folder picker too
        with open(os.path.join(T, "roms", "Game Boy Advance", "Test.gba"), "wb") as f:
            f.write(b"Z" * 1024)

        # Sequence: Pending Queue -> view item -> B -> B -> Scan ROMs -> All Systems -> dismiss -> Status -> A -> Sync Now -> A -> Quit (menu)
        keys = [
            "down", "a",                  # Pending Queue
            "a", "a",                     # Open first item -> dismiss
            "b",                          # Back to Achievements
            "down", "a",                  # Scan ROMs -> folder picker
            "a", "a",                     # All Systems -> dismiss Scan Done
            "down", "down", "a", "a",     # Status -> dismiss
            "up", "up", "up", "up", "a", "a",  # Sync Now -> dismiss result
            "menu",
        ]
        for gba_mini in (False, True):
            # Re-seed queue item for second pass
            if not os.path.isfile(os.path.join(qdir, "01.json")):
                with open(os.path.join(qdir, "01.json"), "w", encoding="utf-8") as f:
                    json.dump({
                        "achievement_id": 500, "game_id": 42,
                        "unlocked_at_utc": _utc(2026, 11, 14, 10, 0, 0),
                        "title": "A Very Long Achievement Name For Screen Width Testing",
                    }, f)
            g = self._run_ra(keys, gba_mini=gba_mini)
            fb = g.fb
            for s in g.seen:
                if s[0] == "message":
                    for line in s[2]:
                        self.assertLessEqual(fb.text_width(line), 240 - 12, f"line too wide: {line!r}")
                    y_end = g._draw_lines(s[2], g.title_h + 6, wifi_gui.Pal.HI)
                    self.assertLessEqual(y_end, g.h - g.foot_h, f"lines overflow height={g.h}: {s[2]!r}")
            if gba_mini:
                self.assertEqual(g.h, 160)
                self.assertEqual(bytes(g.fb.buf[:48 * 480]), b"\x00" * (48 * 480))
                self.assertEqual(bytes(g.fb.buf[208 * 480:]), b"\x00" * (32 * 480))


        # Re-seed queue item + history + game title resolution + busy progress bar
        if not os.path.isfile(os.path.join(qdir, "01.json")):
            with open(os.path.join(qdir, "01.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "achievement_id": 500, "game_id": 42,
                    "unlocked_at_utc": _utc(2026, 11, 14, 10, 0, 0),
                    "title": "A Very Long Achievement Name For Screen Width Testing",
                }, f)
        ra_backend._record_history(
            {"achievement_id": 500, "game_id": 42, "title": "Uploaded Ach", "unlocked_at_utc": _utc(2026, 11, 14, 10, 0)},
            "awarded",
            _utc(2026, 11, 14, 12, 0),
        )
        hist = ra_backend.load_history()
        self.assertGreaterEqual(len(hist), 3)
        self.assertEqual(hist[0]["title"], "Uploaded Ach")
        with open(ra_backend.hashes_path(), "w", encoding="utf-8") as f:
            json.dump({"/mnt/GBA/Game.gba": {"hash": "a" * 32, "game_id": 42, "title": "Pokemon LeafGreen"}}, f)
        self.assertEqual(ra_backend.game_titles_by_id().get(42), "Pokemon LeafGreen")

        # Pending Queue -> view item (shows game title) -> Back -> History -> open entry -> Back -> Quit
        g_hist = self._run_ra(["down", "a", "a", "a", "b", "down", "down", "down", "down", "a", "a", "a", "b", "menu"])
        msgs = [s for s in g_hist.seen if s[0] == "message"]
        self.assertTrue(any("Pokemon LeafGreen" in m[2] for m in msgs))
        self.assertTrue(any(m[1] == "Synced Unlock" for m in msgs))

        # Busy progress bar fits cleanly on both 240x240 and 240x160
        if os.path.exists(os.environ["NANO_FKGPIOD"]):
            os.remove(os.environ["NANO_FKGPIOD"])
        old_btns = wifi_gui.Buttons
        wifi_gui.Buttons = FakeKeys
        try:
            for gba_mini in (False, True):
                if gba_mini:
                    with open(os.environ["NANO_FKGPIOD"], "w", encoding="utf-8") as f:
                        f.write("MAP START+A TO KEY KEY_X\n")
                elif os.path.exists(os.environ["NANO_FKGPIOD"]):
                    os.remove(os.environ["NANO_FKGPIOD"])
                g_prog = RecorderGui()
                g_prog.busy("Scan ROMs", ["Scanning ROMs...", "Metroid Fusion.gba"], hint="B cancel", progress=(450, 3320))
                if gba_mini:
                    self.assertEqual(bytes(g_prog.fb.buf[:48 * 480]), b"\x00" * (48 * 480))
                    self.assertEqual(bytes(g_prog.fb.buf[208 * 480:]), b"\x00" * (32 * 480))
                g_prog.close()
        finally:
            wifi_gui.Buttons = old_btns


if __name__ == "__main__":
    unittest.main()
