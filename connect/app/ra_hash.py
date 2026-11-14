"""Pure-Python RetroAchievements ROM hasher matching rcheevos rc_hash.

Supports cartridge ROMs and .zip archives directly in streaming 64 KB chunks
so memory usage stays minimal on the 64 MB RG Nano. Header stripping follows
RetroAchievements/rcheevos src/hash/rc_hash_rom.c verbatim.
"""
from __future__ import annotations

import hashlib
import os
import zipfile

MAX_HASH_SIZE = 64 * 1024 * 1024
CHUNK_SIZE = 65536

# Map FunKey-OS / RetroFE folder names -> RA console IDs (matching rc_consoles.h)
CONSOLE_FOLDERS = {
    "Game Boy": 4,
    "Game Boy Color": 6,
    "Game Boy Advance": 5,
    "NES": 7,
    "SNES": 3,
    "Sega Genesis": 1,
    "Sega Master System": 11,
    "Game Gear": 15,
    "PCE-TurboGrafx": 8,
    "Atari lynx": 13,
    "Neo Geo Pocket": 14,
    "WonderSwan": 53,
    "Pokemon Mini": 24,
    "PS1": 12,
}

EXT_TO_CONSOLE = {
    ".gb": 4,
    ".dmg": 4,
    ".gbc": 6,
    ".gba": 5,
    ".agb": 5,
    ".nes": 7,
    ".fds": 81,
    ".unf": 7,
    ".unif": 7,
    ".sfc": 3,
    ".smc": 3,
    ".fig": 3,
    ".swc": 3,
    ".bs": 3,
    ".md": 1,
    ".gen": 1,
    ".smd": 1,
    ".32x": 10,
    ".sms": 11,
    ".sg": 33,
    ".gg": 15,
    ".pce": 8,
    ".sgx": 8,
    ".lnx": 13,
    ".ngp": 14,
    ".ngc": 14,
    ".ws": 53,
    ".wsc": 53,
    ".pc2": 53,
    ".min": 24,
}

ROM_EXTENSIONS = frozenset(EXT_TO_CONSOLE.keys()) | {".zip", ".chd", ".pbp", ".cue", ".iso", ".bin", ".m3u"}
SKIP_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".bmp", ".txt", ".nfo", ".sav", ".srm", ".state",
    ".rtc", ".eep", ".fla", ".cfg", ".conf", ".json", ".log", ".bak", ".xml",
    ".ra", ".mcr", ".mcd",
})

# Consoles whose RA hashes strip copier/format headers per rc_hash_rom.c
NES_CONSOLES = frozenset({7, 81})
SNES_CONSOLE = 3
PCE_CONSOLE = 8
LYNX_CONSOLE = 13


def infer_console_id(path: str, folder_name: str | None = None) -> int | None:
    ext = os.path.splitext(path)[1].lower()
    if ext in EXT_TO_CONSOLE:
        return EXT_TO_CONSOLE[ext]
    if folder_name and folder_name in CONSOLE_FOLDERS:
        return CONSOLE_FOLDERS[folder_name]
    parent = os.path.basename(os.path.dirname(path))
    if parent in CONSOLE_FOLDERS:
        return CONSOLE_FOLDERS[parent]
    return None


def _header_skip_bytes(head: bytes, total_size: int, console_id: int | None) -> int:
    if console_id in NES_CONSOLES:
        if total_size > 16 and (head[:4] == b"NES\x1a" or head[:4] == b"FDS\x1a"):
            return 16
    elif console_id == SNES_CONSOLE:
        if (total_size % 0x2000) == 512 and total_size > 512:
            return 512
    elif console_id == PCE_CONSOLE:
        if (total_size & 512) and total_size > 512:
            return 512
    elif console_id == LYNX_CONSOLE:
        if total_size > 64 and head[:5] == b"LYNX\x00":
            return 64
    return 0


def _hash_stream(stream, total_size: int, console_id: int | None) -> str:
    first = stream.read(CHUNK_SIZE)
    if not first:
        return hashlib.md5(b"").hexdigest()
    skip = _header_skip_bytes(first, total_size, console_id)
    h = hashlib.md5()
    remaining = min(total_size, MAX_HASH_SIZE)
    if skip >= len(first):
        to_discard = skip - len(first)
        if to_discard:
            stream.read(to_discard)
        remaining -= skip
    else:
        view = memoryview(first)[skip:min(len(first), remaining)]
        h.update(view)
        remaining -= len(first)
        if remaining < 0:
            remaining = 0

    while remaining > 0:
        chunk = stream.read(min(CHUNK_SIZE, remaining))
        if not chunk:
            break
        h.update(chunk)
        remaining -= len(chunk)
    return h.hexdigest()


def _pick_zip_member(zf: zipfile.ZipFile) -> zipfile.ZipInfo | None:
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if not infos:
        return None
    for info in infos:
        ext = os.path.splitext(info.filename)[1].lower()
        if ext in EXT_TO_CONSOLE:
            return info
    for info in infos:
        ext = os.path.splitext(info.filename)[1].lower()
        if ext not in SKIP_EXTENSIONS:
            return info
    return infos[0]


def hash_rom(path: str, console_id: int | None = None, folder_name: str | None = None) -> tuple[str | None, int | None]:
    """Return (ra_md5_hex_lower, console_id) or (None, console_id) if unsupported (e.g. disc image)."""
    ext = os.path.splitext(path)[1].lower()
    cid = console_id if console_id is not None else infer_console_id(path, folder_name)

    if ext in (".chd", ".pbp", ".cue", ".iso", ".m3u"):
        return None, cid

    if ext == ".zip":
        with zipfile.ZipFile(path, "r") as zf:
            member = _pick_zip_member(zf)
            if member is None:
                return None, cid
            inner_cid = infer_console_id(member.filename, folder_name) or cid
            with zf.open(member, "r") as mf:
                return _hash_stream(mf, member.file_size, inner_cid), inner_cid

    size = os.path.getsize(path)
    with open(path, "rb") as f:
        return _hash_stream(f, size, cid), cid
