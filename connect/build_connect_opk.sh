#!/bin/bash
# Builds the Connect OPK (Wi-Fi + USB Link + USB Mode in one app) and installs
# it in the Applications menu over adb.
#
#   ./build_connect_opk.sh              build it and install it on the Nano
#   ./build_connect_opk.sh --refresh    ...and restart gmenu2x so it shows now
#   ./build_connect_opk.sh --build-only just build, do not touch the device
#   ./build_connect_opk.sh --retire-old on the Nano, park the apps Connect
#                                       replaces (WiFi, USBLink, SwitchToUSB,
#                                       SwitchToADB) as *.opk.off; nothing is
#                                       deleted, rename them back to undo
#
# The binaries (kernel modules, wpa_supplicant, the Python runtime) are the ones
# wifi/build_wifi_opk.sh builds: this takes them from its work directory
# (WIFI_WORK, default ~/.cache/rg-nano-adb-tools/wifi) and runs that build first
# if they are not there yet. The scripts and the app come straight from this
# repository, so a change to them only needs this script again.
#
# Handheld Link is bundled when its OPK is at hand: HLINK_OPK=<HandheldLink.opk>,
# or a handheld-usb-link checkout next to this repository.
#
# Needs: bash, mksquashfs and unsquashfs (squashfs-tools), python3 (for the
# icon), and what wifi/build_wifi_opk.sh needs if the binaries have to be built.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$HERE/.."
REMOTE="$REPO/nanoctl"
WORK="${WIFI_WORK:-${XDG_CACHE_HOME:-$HOME/.cache}/rg-nano-adb-tools/wifi}"
OUT="$HERE/Connect.opk"
APPS="/mnt/Applications"

MODE="${1:-}"
case "$MODE" in ""|--refresh|--build-only|--retire-old) ;; *) sed -n '2,24p' "$0"; exit 2 ;; esac

if [ "$MODE" = "--retire-old" ]; then
  # Renames only. The entry for the mode the Nano is not in is parked already.
  "$REMOTE" run "cd $APPS && for a in WiFi USBLink SwitchToUSB SwitchToADB; do [ -e \$a.opk ] && mv \$a.opk \$a.opk.off && echo \"parked \$a.opk\"; done; ls $APPS"
  echo "Restart the menu to see the change (pkill gmenu2x, or reboot the Nano)."
  exit 0
fi

command -v mksquashfs >/dev/null || { echo "mksquashfs is required (apt install squashfs-tools)" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required to draw the icon" >&2; exit 1; }

BIN="$WORK/opk"    # the stage wifi/build_wifi_opk.sh leaves behind
have_binaries() {
  [ -x "$BIN/rt/bin/python3" ] && [ -f "$BIN/modules/rtl8188fu.ko" ] && [ -f "$BIN/modules/cfg80211.ko" ] \
    && [ -x "$BIN/bin/wpa_supplicant" ] && [ -f "$BIN/firmware/rtl8188fufw.bin" ]
}
if ! have_binaries; then
  echo "### the Wi-Fi binaries are not built yet: running wifi/build_wifi_opk.sh --build-only"
  "$REPO/wifi/build_wifi_opk.sh" --build-only
  have_binaries || { echo "build_connect_opk: the Wi-Fi build left no binaries in $BIN" >&2; exit 1; }
fi

# The stage sits next to the binaries so they can be hard links: the runtime is
# 25 MB and a second copy for every build is space this needs not use
STAGE="$WORK/connect-opk"
rm -rf "$STAGE"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/app"
for d in bin modules firmware rt; do
  cp -al "$BIN/$d" "$STAGE/$d" 2>/dev/null || cp -a "$BIN/$d" "$STAGE/$d"
done

# CRLF-safe copies: these run under busybox sh on the Nano
script() { sed 's/\r$//' "$1" > "$STAGE/$2"; chmod +x "$STAGE/$2"; }
script "$HERE/run.sh" run.sh
script "$REPO/wifi/wifi.sh" wifi.sh
script "$REPO/usblink/usblink.sh" usblink.sh
script "$REPO/usbmode/usbmode.sh" usbmode.sh
sed 's/\r$//' "$REPO/wifi/kernel/kernels" > "$STAGE/kernels"
for f in "$REPO/wifi/app/"*.py "$HERE/app/"*.py; do
  sed 's/\r$//' "$f" > "$STAGE/app/$(basename "$f")"
done
python3 "$REPO/usbmode/make_icon.py" connect "$STAGE/connect.png" >/dev/null

# Handheld Link (file sharing between handhelds, from the handheld-usb-link
# project), taken as built from its own OPK: the app, the script that gives the
# USB gadget a network function and the USB network drivers. It uses the same
# Python runtime, so it adds about 0.3 MB. Without that OPK, Connect is built
# without the Handheld Link row.
HLINK_OPK="${HLINK_OPK:-}"
if [ -z "$HLINK_OPK" ]; then
  for d in "$REPO/../handheld-usb-link" "$REPO/../handheld-usb-netplay"; do
    [ -f "$d/handheld-link/funkey/HandheldLink.opk" ] && HLINK_OPK="$d/handheld-link/funkey/HandheldLink.opk" && break
  done
fi
if [ -n "$HLINK_OPK" ] && [ -f "$HLINK_OPK" ]; then
  command -v unsquashfs >/dev/null || { echo "unsquashfs is required to take Handheld Link from $HLINK_OPK" >&2; exit 1; }
  unsquashfs -q -n -d "$STAGE/hl" "$HLINK_OPK" hlink net gadget.sh >/dev/null
  [ -f "$STAGE/hl/hlink/main.pyc" ] || { echo "build_connect_opk: no hlink app in $HLINK_OPK" >&2; exit 1; }
  mv "$STAGE/hl/hlink" "$STAGE/hlink"
  mv "$STAGE/hl/gadget.sh" "$STAGE/gadget.sh"
  [ -d "$STAGE/hl/net" ] && mv "$STAGE/hl/net" "$STAGE/net"
  rm -rf "$STAGE/hl"
  f="$(dirname "$HLINK_OPK")/../FONT-LICENSE.txt"
  [ -f "$f" ] && cp "$f" "$STAGE/hlink/FONT-LICENSE.txt"
  echo "with Handheld Link from $HLINK_OPK"
else
  echo "without Handheld Link (no HandheldLink.opk found; set HLINK_OPK to bundle it)"
fi

cat > "$STAGE/connect.funkey-s.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Connect
Comment=Wi-Fi and USB connections
Exec=run.sh
Icon=connect
Terminal=false
Categories=applications;
EOF

mksquashfs "$STAGE" "$OUT" -all-root -noappend -no-exports -no-xattrs -comp gzip >/dev/null
echo "built $OUT ($(wc -c < "$OUT") bytes)"

case "$MODE" in
  --build-only) exit 0 ;;
esac

"$REMOTE" push "$OUT" "$APPS/Connect.opk"
if [ "$MODE" = "--refresh" ]; then
  "$REMOTE" run 'pkill gmenu2x && echo "menu restarted" || echo "gmenu2x is not running (an app is open?); the entry appears the next time the menu starts"'
else
  echo "installed. Restart the menu to see it (re-run with --refresh, or reboot the Nano)."
fi
echo "The apps it replaces are still there: --retire-old parks them."
