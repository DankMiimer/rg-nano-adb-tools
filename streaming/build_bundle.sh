#!/bin/bash
# Build an SD-card ZIP. Never contacts or changes the Nano.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-$HERE/dist}"
RUNTIME="${NANO_PY_RUNTIME:-${XDG_CACHE_HOME:-$HOME/.cache}/rg-nano-adb-tools/wifi/runtime}"
YTDLP_VERSION=2026.10.26
YTDLP_SHA=e51e69e89489f199530f13d32a4342da3ddb2cf34842f364603a066db05afbf3
for t in python3 mksquashfs; do command -v "$t" >/dev/null || { echo "$t is required" >&2; exit 1; }; done
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
CARD="$WORK/bundle/sd-card"
mkdir -p "$CARD/FunKey/youtube" "$CARD/FunKey/nrk" "$CARD/Native games" "$WORK/np" "$WORK/nrk"
cp "$HERE/youtube/youtube.sh" "$HERE/youtube/youtube_nano.py" "$HERE/youtube/yt-dlp" "$CARD/FunKey/youtube/"
cp "$HERE/nrk/nrklive.sh" "$HERE/nrk/RasterForgeRegular-JpBgm.ttf" "$HERE/nrk/"p?_bg.mp4 "$CARD/FunKey/nrk/"

# The snapshot path copies only the installed extractor, never data/cache/cookies.
if [ -n "${YTDLP_DIR:-}" ]; then
  cp -a "$YTDLP_DIR" "$CARD/FunKey/youtube/yt-dlp.d"
else
  python3 -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))' || { echo 'Python 3.12 is required to precompile yt-dlp' >&2; exit 1; }
  command -v curl >/dev/null
  curl -fL --retry 3 "https://github.com/yt-dlp/yt-dlp/releases/download/$YTDLP_VERSION/yt-dlp" -o "$WORK/yt-dlp.zip"
  echo "$YTDLP_SHA  $WORK/yt-dlp.zip" | sha256sum -c -
  python3 -I -c 'import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$WORK/yt-dlp.zip" "$CARD/FunKey/youtube/yt-dlp.d"
  rm -f "$CARD/FunKey/youtube/yt-dlp.d/__main__.py"
  python3 -m compileall -b -q "$CARD/FunKey/youtube/yt-dlp.d"
  find "$CARD/FunKey/youtube/yt-dlp.d" -name '*.py' -delete
fi

if [ -n "${AESDEC_BIN:-}" ]; then
  cp "$AESDEC_BIN" "$CARD/FunKey/nrk/aesdec"
else
  CC="${NANO_CC:-${FUNKEY_SDK:-$HOME/FunKey-sdk-2.3.0}/bin/arm-funkey-linux-musleabihf-gcc}"
  "$CC" -static -O2 "$HERE/nrk/aesdec.c" -o "$CARD/FunKey/nrk/aesdec"
fi

# Supplying OPKs preserves the installed packages byte for byte. Otherwise build
# the same launchers, using the Python/qjs runtime built by wifi/build_wifi_opk.sh.
if [ -n "${NANOPLAY_OPK:-}" ]; then
  cp "$NANOPLAY_OPK" "$CARD/Native games/NanoPlay.opk"
else
  [ -x "$RUNTIME/bin/python3" ] && [ -x "$RUNTIME/bin/qjs" ] || { echo 'Build wifi/build_wifi_opk.sh --build-only first, or set NANO_PY_RUNTIME' >&2; exit 1; }
  cp -a "$RUNTIME" "$WORK/np/rt"
  cp "$HERE/youtube/run.sh" "$HERE/youtube/default.funkey-s.desktop" "$HERE/youtube/nanoplay.png" "$WORK/np/"
  # Stock RG Nano has /usr/bin/st. Optionally bundle it for firmwares without it.
  if [ -n "${NANO_ST:-}" ]; then cp "$NANO_ST" "$WORK/np/st"; chmod +x "$WORK/np/st"; fi
  chmod +x "$WORK/np/run.sh"
  mksquashfs "$WORK/np" "$CARD/Native games/NanoPlay.opk" -all-root -noappend -no-exports -no-xattrs -comp gzip >/dev/null
fi
if [ -n "${NRK_OPK:-}" ]; then
  cp "$NRK_OPK" "$CARD/Native games/NRK.opk"
else
  cp "$HERE/nrk/run.sh" "$HERE/nrk/default.funkey-s.desktop" "$HERE/nrk/nrk.png" "$WORK/nrk/"
  chmod +x "$WORK/nrk/run.sh"
  mksquashfs "$WORK/nrk" "$CARD/Native games/NRK.opk" -all-root -noappend -no-exports -no-xattrs -comp gzip >/dev/null
fi
cp "$HERE/youtube/nanoplay.png" "$CARD/Native games/NanoPlay.png"
cp "$HERE/nrk/nrk.png" "$CARD/Native games/NRK.png"
chmod +x "$CARD/FunKey/youtube/"{youtube.sh,yt-dlp} "$CARD/FunKey/nrk/"{nrklive.sh,aesdec}
cp "$HERE/README.md" "$WORK/bundle/README.md"
cp "$HERE/THIRD_PARTY.md" "$WORK/bundle/THIRD_PARTY.md"
cp "$HERE/../LICENSE" "$WORK/bundle/LICENSE"
cp -a "$HERE/licenses" "$WORK/bundle/licenses"
# Include the decryptor source alongside the executable.
mkdir -p "$WORK/bundle/source"
cp "$HERE/nrk/aesdec.c" "$WORK/bundle/source/"
python3 - "$WORK/bundle" "$OUT/rg-nano-streaming.zip" <<'PY'
import hashlib, pathlib, stat, sys, zipfile
root, out = map(pathlib.Path, sys.argv[1:])
files = sorted(p for p in root.rglob('*') if p.is_file())
# Refuse accidental inclusion of device state, regardless of how inputs were staged.
for p in files:
    rel = p.relative_to(root)
    if p.name.endswith(('.log', '.key')) or p.name == 'cookies.txt' or any(part in ('cache', 'data', '__pycache__') for part in rel.parts):
        raise SystemExit(f'Refusing private/generated state: {rel}')
manifest = ''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(root).as_posix()}\n' for p in files)
(root / 'SHA256SUMS.txt').write_text(manifest, encoding='utf-8')
with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for p in sorted(root.rglob('*')):
        if p.is_file():
            info = zipfile.ZipInfo(p.relative_to(root).as_posix(), (2026, 11, 7, 0, 0, 0))
            info.create_system = 3
            mode = 0o755 if p.name in ('youtube.sh', 'yt-dlp', 'nrklive.sh', 'aesdec') else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, p.read_bytes())
(out.parent / 'SHA256SUMS.txt').write_text(f'{hashlib.sha256(out.read_bytes()).hexdigest()}  {out.name}\n', encoding='utf-8')
print(f'Built {out} ({out.stat().st_size:,} bytes); no device files were changed.')
PY
