#!/usr/bin/env python3
"""Check that every symbol some kernel modules import is exported by a kernel.

    audit_exports.py <vmlinux> <module.ko>...

<vmlinux> is the decompressed kernel image (not an ELF: build_wifi_opk.sh
--audit pulls /boot/zImage off the Nano and unpacks it). Its export table is
recovered from the __ksymtab entries, (value, name pointer) pairs: the right
load address resolves thousands of them to C identifiers and a wrong one
almost none. Symbols one of the given modules exports itself count as
resolved. Exit status 1 if anything is missing.

A missing export would only make insmod fail; what protects against a struct
layout mismatch (a crash) is building with the kernel's configuration, and
loading only into kernels listed in `kernels`.
"""
import re
import struct
import subprocess
import sys
from pathlib import Path


def exports(blob, base):
    found = {}
    for off in range(0, len(blob) - 8, 4):
        value, ptr = struct.unpack_from("<II", blob, off)
        so = ptr - base
        if not (0xC0000000 <= value < 0xD0000000 and 0 <= so < len(blob)):
            continue
        end = blob.find(b"\0", so, so + 160)
        if end < 0:
            continue
        name = blob[so:end]
        if re.fullmatch(rb"[A-Za-z_][A-Za-z0-9_]*", name):
            found[name.decode()] = value
    return found


def nm(args):
    return subprocess.check_output([NM] + args, text=True).splitlines()


if len(sys.argv) < 3:
    sys.exit(__doc__)
NM = "arm-funkey-linux-musleabihf-nm"
blob = Path(sys.argv[1]).read_bytes()
ident = re.search(rb"Linux version [ -~]{0,150}", blob)
print("kernel:", ident.group(0).decode() if ident else "?")
table = max((exports(blob, b) for b in (0xC0008000, 0xC0000000, 0xC0208000)), key=len)
print(f"  {len(table)} exports")
if len(table) < 500:
    sys.exit("no plausible export table found")

mods = [Path(p) for p in sys.argv[2:]]
provided = {l.split()[-1][len("__ksymtab_"):] for m in mods for l in nm([str(m)])
            if l.split()[-1].startswith("__ksymtab_")}
missing = set()
for m in mods:
    imports = [l.split()[-1] for l in nm(["-u", str(m)]) if l.split()[0] == "U"]
    unresolved = sorted(set(imports) - table.keys() - provided)
    missing.update(unresolved)
    print(f"  {m.name:16} imports {len(imports):4}  unresolved {unresolved or 'none'}")
print("UNRESOLVED:", sorted(missing) or "none")
sys.exit(1 if missing else 0)
