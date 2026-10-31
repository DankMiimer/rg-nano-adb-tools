#!/bin/bash
# Builds the Wi-Fi OPK for the stock RG Nano firmware and installs it in the
# Applications menu over adb.
#
#   ./build_wifi_opk.sh              build it and install it on the Nano
#   ./build_wifi_opk.sh --refresh    ...and restart gmenu2x so it shows now
#   ./build_wifi_opk.sh --build-only just build, do not touch the device
#   ./build_wifi_opk.sh --audit      first check the modules against the
#                                    connected Nano's kernel (pulls /boot/zImage)
#
# Builds, from pinned sources (each download is checked against its SHA-256):
#   - cfg80211 and the rtl8188fu driver, as modules for the factory kernel:
#     DrUm78's RG Nano kernel source with kernel/config (the factory
#     configuration plus cfg80211 as a module, without wireless extensions,
#     which would change core structures), and kelebek333/rtl8188fu
#   - wpa_supplicant / wpa_cli 2.11 (nl80211, against the firmware's libnl)
#   - a Python 3.12 runtime for the settings app (OpenSSL 1.1.1w, CA bundle,
#     qjs); NanoPlay-style apps can reuse it from $WORK/runtime
#
# Needs: the FunKey SDK 2.3.0 (FUNKEY_SDK, default ~/FunKey-sdk-2.3.0), bash,
# curl, git, make, gcc, bc, python3 3.12 (it compiles the runtime's .pyc),
# mksquashfs, and lzop for --audit. Work files go to WIFI_WORK (default
# ~/.cache/rg-nano-adb-tools/wifi: a kernel tree needs about 1 GB).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE="$HERE/../nanoctl"
SDK="${FUNKEY_SDK:-$HOME/FunKey-sdk-2.3.0}"
WORK="${WIFI_WORK:-${XDG_CACHE_HOME:-$HOME/.cache}/rg-nano-adb-tools/wifi}"
OUT="$HERE/WiFi.opk"
APPS="/mnt/Applications"
T=arm-funkey-linux-musleabihf
J="$(nproc 2>/dev/null || echo 4)"
export PATH="$SDK/bin:$PATH"

MODE="${1:-}"
case "$MODE" in ""|--refresh|--build-only|--audit) ;; *) sed -n '2,25p' "$0"; exit 2 ;; esac

# DrUm78's RG Nano kernel (Linux 4.14.14 + the FunKey/RG Nano changes), the
# source of the factory kernel. Fetched with git, which checks every object
# against the commit hash. LINUX_SRC=<dir> uses a tree you already have.
LINUX_COMMIT=1f6d3fc2f17bc1d31aa2cf89d5642da8bbf4517b
LINUX_GIT=https://github.com/DrUm78/linux.git
RTL_COMMIT=c8c95708b3756c67139c456a2a6576c1e6491d82
RTL_URL=https://github.com/kelebek333/rtl8188fu/archive/$RTL_COMMIT.tar.gz
RTL_SHA=c960a881de261aa8065ac1a4277664d2e9f31187c6050c2c75d386e7832bb2df
WPA_URL=https://w1.fi/releases/wpa_supplicant-2.11.tar.gz
WPA_SHA=912ea06f74e30a8e36fbb68064d6cdff218d8d591db0fc5d75dee6c81ac7fc0a
SSL_URL=https://www.openssl.org/source/openssl-1.1.1w.tar.gz
SSL_SHA=cf3098950cb4d853ad95c0841f1f9c6d3dc102dccfcacd521d93925208b76ac8
PY_URL=https://www.python.org/ftp/python/3.12.12/Python-3.12.12.tgz
PY_SHA=487c908ddf4097a1b9ba859f25fe46d22ccaabfb335880faac305ac62bffb79b
QJS_URL=https://bellard.org/quickjs/quickjs-2025-04-26.tar.xz
QJS_SHA=2f20074c25166ef6f781f381c50d57b502cb85d470d639abccebbef7954c83bf

step() { echo; echo "### $*"; }
die() { echo "build_wifi_opk: $*" >&2; exit 1; }

command -v $T-gcc >/dev/null || die "FunKey SDK not found (set FUNKEY_SDK; looked in $SDK)"
for t in curl make gcc bc mksquashfs; do command -v $t >/dev/null || die "$t is required"; done
PYHOST="$(command -v python3.12 || command -v python3)"
"$PYHOST" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))' \
  || die "python3 3.12 is required (the runtime's .pyc are compiled with it)"
SYSROOT="$($T-gcc -print-sysroot)"

# fetch NAME URL SHA256: download once into its own directory, check the hash,
# print the path
fetch() {
  local d="$WORK/dl/$1" f
  f="$d/$(basename "$2")"
  mkdir -p "$d"
  if [ ! -s "$f" ]; then
    curl -fL --retry 3 -o "$f.part" "$2" || die "download failed: $2"
    mv "$f.part" "$f"
  fi
  echo "$3  $f" | sha256sum -c --quiet - >&2 || die "checksum mismatch for $f"
  echo "$f"
}

# unpack ARCHIVE DIR: a fresh copy of a source tree
unpack() { rm -rf "$2"; mkdir -p "$2"; tar xf "$1" -C "$2" --strip-components=1; }

mkdir -p "$WORK"
KFLAGS='-march=armv7-a -mcpu=cortex-a7 -fno-common'
# 4.14 builds everything with -fcommon; the module loader refuses COMMON
# symbols, so modules get -fno-common on top
KMAKE=(make ARCH=arm CROSS_COMPILE=$T- HOSTCFLAGS=-fcommon "KCFLAGS=$KFLAGS")

step "kernel modules: cfg80211"
L="${LINUX_SRC:-$WORK/linux}"
if [ -z "${LINUX_SRC:-}" ] && [ ! -f "$L/.fetched-$LINUX_COMMIT" ]; then
  command -v git >/dev/null || die "git is required to fetch the kernel source"
  rm -rf "$L"; mkdir -p "$L"
  ( cd "$L" && git init -q && git fetch -q --depth 1 "$LINUX_GIT" "$LINUX_COMMIT" \
    && git -c advice.detachedHead=false checkout -q FETCH_HEAD ) || die "could not fetch the kernel source"
  rm -rf "$L/.git"
  touch "$L/.fetched-$LINUX_COMMIT"
fi
K="$WORK/kbuild"
rm -rf "$K"; mkdir -p "$K"
cp "$HERE/kernel/config" "$K/.config"
"${KMAKE[@]}" -C "$L" O="$K" olddefconfig >/dev/null
cmp -s <(grep '^CONFIG_' "$HERE/kernel/config" | sort) <(grep '^CONFIG_' "$K/.config" | sort) \
  || die "olddefconfig changed kernel/config; the modules would not match the factory kernel"
"${KMAKE[@]}" -C "$L" O="$K" -j"$J" modules_prepare >"$WORK/kernel.log" 2>&1 || { tail -20 "$WORK/kernel.log"; die "modules_prepare failed"; }
"${KMAKE[@]}" -C "$L" O="$K" -j"$J" M=net/wireless modules >>"$WORK/kernel.log" 2>&1 || { tail -20 "$WORK/kernel.log"; die "cfg80211 failed"; }

step "kernel modules: rtl8188fu"
R="$WORK/rtl8188fu"
unpack "$(fetch rtl8188fu "$RTL_URL" "$RTL_SHA")" "$R"
"${KMAKE[@]}" -C "$L" O="$K" M="$R" CONFIG_RTL8188FU=m KBUILD_EXTRA_SYMBOLS="$K/net/wireless/Module.symvers" \
  -j"$J" modules >"$WORK/rtl8188fu.log" 2>&1 || { tail -20 "$WORK/rtl8188fu.log"; die "rtl8188fu failed"; }
for m in "$K/net/wireless/cfg80211.ko" "$R/rtl8188fu.ko"; do
  v="$(modinfo -F vermagic "$m")"
  [ "$v" = "4.14.14-funkey SMP mod_unload ARMv7 p2v8 " ] || die "unexpected vermagic [$v] in $m"
  [ -z "$($T-nm "$m" | awk '$2 == "C"')" ] || die "COMMON symbols in $m (the loader refuses them)"
done

if [ "$MODE" = --audit ]; then
  step "audit against the connected Nano's kernel"
  command -v lzop >/dev/null || die "lzop is required for --audit"
  A="$WORK/audit"; rm -rf "$A"; mkdir -p "$A"
  "$REMOTE" pull /boot/zImage "$A/zImage" >/dev/null
  # the image wraps an LZO payload; try each lzop magic, keep what decompresses
  for off in $("$PYHOST" -c 'import sys
b = open(sys.argv[1], "rb").read(); i = b.find(b"\x89LZO\0\r\n\x1a\n")
while i >= 0: print(i); i = b.find(b"\x89LZO\0\r\n\x1a\n", i + 1)' "$A/zImage"); do
    tail -c +$((off + 1)) "$A/zImage" > "$A/payload.lzo"
    lzop -d -f -o "$A/vmlinux" "$A/payload.lzo" 2>/dev/null || true   # trailing data makes lzop fail
    grep -aq "Linux version" "$A/vmlinux" 2>/dev/null && break
  done
  "$PYHOST" "$HERE/kernel/audit_exports.py" "$A/vmlinux" "$K/net/wireless/cfg80211.ko" "$R/rtl8188fu.ko"
fi

step "wpa_supplicant"
W="$WORK/wpa"
unpack "$(fetch wpa "$WPA_URL" "$WPA_SHA")" "$W"
cat > "$W/wpa_supplicant/.config" <<'EOF'
CONFIG_DRIVER_NL80211=y
CONFIG_LIBNL32=y
CONFIG_CTRL_IFACE=y
CONFIG_BACKEND=file
CONFIG_TLS=internal
CONFIG_INTERNAL_LIBTOMMATH=y
CONFIG_ELOOP=eloop
CONFIG_L2_PACKET=linux
CONFIG_OS=unix
CONFIG_SME=y
CONFIG_IEEE80211W=y
CONFIG_IEEE80211N=y
CONFIG_NO_RANDOM_POOL=y
EOF
make -C "$W/wpa_supplicant" -j"$J" CC=$T-gcc PKG_CONFIG=pkg-config \
  EXTRA_CFLAGS="-Os -I$SYSROOT/usr/include/libnl3" LDFLAGS="-L$SYSROOT/usr/lib" \
  wpa_supplicant wpa_cli >"$WORK/wpa.log" 2>&1 || { tail -20 "$WORK/wpa.log"; die "wpa_supplicant failed"; }

step "OpenSSL 1.1.1 (for Python's ssl)"
S="$WORK/openssl"
unpack "$(fetch openssl "$SSL_URL" "$SSL_SHA")" "$S"
( cd "$S" && ./Configure linux-armv4 --cross-compile-prefix=$T- --prefix="$WORK/stage/openssl" \
    --openssldir=/etc/ssl shared no-tests no-async -march=armv7-a -mtune=cortex-a7 \
  && make -j"$J" && make install_sw ) >"$WORK/openssl.log" 2>&1 || { tail -20 "$WORK/openssl.log"; die "OpenSSL failed"; }

step "Python 3.12"
P="$WORK/python"
unpack "$(fetch python "$PY_URL" "$PY_SHA")" "$P"
printf 'ac_cv_file__dev_ptmx=yes\nac_cv_file__dev_ptc=no\nac_cv_buggy_getaddrinfo=no\n' > "$P/config.site"
( cd "$P" && CONFIG_SITE="$P/config.site" \
    CPPFLAGS="-I$SYSROOT/usr/include -I$WORK/stage/openssl/include" \
    LDFLAGS="-L$SYSROOT/usr/lib -L$WORK/stage/openssl/lib" \
    CFLAGS="-Os -march=armv7-a -mtune=cortex-a7" \
    ./configure --build="$(gcc -dumpmachine)" --host=$T --prefix=/usr \
      --with-build-python="$PYHOST" --with-openssl="$WORK/stage/openssl" \
      --without-ensurepip --disable-test-modules --with-lto=no \
  && make -j"$J" && rm -rf "$WORK/stage/python" && make install DESTDIR="$WORK/stage/python" ) \
  >"$WORK/python.log" 2>&1 || { tail -30 "$WORK/python.log"; die "Python failed"; }

step "QuickJS (qjs, for yt-dlp in apps that reuse the runtime)"
Q="$WORK/quickjs"
unpack "$(fetch quickjs "$QJS_URL" "$QJS_SHA")" "$Q"
make -C "$Q" -j"$J" CROSS_PREFIX=$T- HOST_CC=gcc CONFIG_LTO= qjs >"$WORK/qjs.log" 2>&1 \
  || { tail -20 "$WORK/qjs.log"; die "qjs failed"; }

step "runtime: trimmed, precompiled Python 3.12 + OpenSSL + qjs + CA bundle"
RT="$WORK/runtime"
rm -rf "$RT"; mkdir -p "$RT/bin" "$RT/lib" "$RT/etc"
cp "$WORK/stage/python/usr/bin/python3.12" "$RT/bin/python3"
ln -s python3 "$RT/bin/python3.12"
cp -a "$WORK/stage/python/usr/lib/python3.12" "$RT/lib/"
(
  cd "$RT/lib/python3.12"
  rm -rf test idlelib tkinter turtledemo turtle.py ensurepip lib2to3 pydoc_data config-3.12-* venv __phello__
  find . -name __pycache__ -prune -exec rm -rf {} +
  find . -type d \( -name tests -o -name test \) -prune -exec rm -rf {} +
  rm -f lib-dynload/_test*.so lib-dynload/xxlimited*.so lib-dynload/_ctypes_test*.so lib-dynload/_curses*.so \
        lib-dynload/_xxinterpchannels*.so lib-dynload/_xxsubinterpreters*.so lib-dynload/ossaudiodev*.so \
        lib-dynload/spwd*.so
  # sourceless: the Nano can't write .pyc to a squashfs, and compiling the
  # stdlib on its single core on every start would take seconds
  "$PYHOST" -m compileall -b -q -j "$J" -d /usr/lib/python3.12 . >/dev/null
  find . -name '*.py' -delete
)
cp -a "$WORK/stage/openssl/lib/libssl.so.1.1" "$WORK/stage/openssl/lib/libcrypto.so.1.1" "$RT/lib/"
cp "$Q/qjs" "$RT/bin/qjs"
$T-strip "$RT/bin/python3" "$RT/bin/qjs" "$RT/lib/"*.so.1.1 "$RT/lib/python3.12/lib-dynload/"*.so
CA=/etc/ssl/certs/ca-certificates.crt
[ -s "$CA" ] || die "no CA bundle at $CA (install ca-certificates)"
cp "$CA" "$RT/etc/cacert.pem"

step "OPK"
STAGE="$WORK/opk"
rm -rf "$STAGE"; mkdir -p "$STAGE/app" "$STAGE/bin" "$STAGE/modules" "$STAGE/firmware"
for f in run.sh wifi.sh; do sed 's/\r$//' "$HERE/$f" > "$STAGE/$f"; chmod +x "$STAGE/$f"; done
sed 's/\r$//' "$HERE/kernel/kernels" > "$STAGE/kernels"
cp "$HERE/app/"*.py "$STAGE/app/"
cp "$W/wpa_supplicant/wpa_supplicant" "$W/wpa_supplicant/wpa_cli" "$STAGE/bin/"
$T-strip "$STAGE/bin/"*
cp "$K/net/wireless/cfg80211.ko" "$R/rtl8188fu.ko" "$STAGE/modules/"
$T-strip --strip-debug "$STAGE/modules/"*.ko
cp "$R/firmware/rtl8188fufw.bin" "$STAGE/firmware/"
cp -a "$RT" "$STAGE/rt"
"$PYHOST" "$HERE/../usbmode/make_icon.py" wifi "$STAGE/wifi.png" >/dev/null
cat > "$STAGE/wifi.funkey-s.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Wi-Fi
Comment=Wi-Fi with a USB dongle
Exec=run.sh
Icon=wifi
Terminal=false
Categories=applications;
EOF
mksquashfs "$STAGE" "$OUT" -all-root -noappend -no-exports -no-xattrs -comp gzip >/dev/null
echo "built $OUT ($(wc -c < "$OUT") bytes); the Python runtime is in $RT"

case "$MODE" in
  --build-only) exit 0 ;;
esac

"$REMOTE" push "$OUT" "$APPS/WiFi.opk"
if [ "$MODE" = "--refresh" ]; then
  "$REMOTE" run 'pkill gmenu2x && echo "menu restarted" || echo "gmenu2x is not running (an app is open?); the entry appears the next time the menu starts"'
else
  echo "installed. Restart the menu to see it (re-run with --refresh, or reboot the Nano)."
fi
