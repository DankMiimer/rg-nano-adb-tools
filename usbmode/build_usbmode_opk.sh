#!/bin/bash
# Builds the two USB Mode OPKs and installs them in the Applications menu over adb.
#
#   "Switch to USB"  SwitchToUSB.opk  USB symbol   shown while the Nano is in ADB mode
#   "Switch to ADB"  SwitchToADB.opk  ">_ ADB"     shown while the Nano is in storage mode
#
# Only one is visible at a time; the other is parked as SwitchTo*.opk.off. usbmode.sh
# swaps them (by renaming) every time it switches mode. This script installs them
# for the mode the Nano is in *now*, and removes the old single-entry USBMode.opk.
#
#   ./build_usbmode_opk.sh              build both and install them on the Nano
#   ./build_usbmode_opk.sh --refresh    ...and restart gmenu2x so the change shows now
#   ./build_usbmode_opk.sh --build-only just build, do not touch the device
#
# Needs: bash, mksquashfs (squashfs-tools), python3, and nanoctl (for the push).
# The Nano must be in ADB mode to install; --build-only needs no device.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE="$HERE/../nanoctl"
APPS="/mnt/Applications"   # /media is a symlink to /mnt on the device

command -v mksquashfs >/dev/null || { echo "mksquashfs is required (apt install squashfs-tools)" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required to draw the icons" >&2; exit 1; }

STAGE_ROOT="$(mktemp -d)"
trap 'rm -rf "$STAGE_ROOT"' EXIT

# build_variant <USB|ADB> <menu name> <comment>
build_variant() {
  local id="$1" name="$2" comment="$3" lower stage out
  lower="$(printf '%s' "$id" | tr 'A-Z' 'a-z')"
  stage="$STAGE_ROOT/$id"
  out="$HERE/SwitchTo$id.opk"
  mkdir -p "$stage"

  # CRLF-safe copy: the script runs under busybox sh on the Nano.
  sed 's/\r$//' "$HERE/usbmode.sh" > "$stage/usbmode.sh"
  chmod +x "$stage/usbmode.sh"
  python3 "$HERE/make_icon.py" "$lower" "$stage/usbmode.png" >/dev/null

  cat > "$stage/usbmode.funkey-s.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$name
Comment=$comment
Exec=usbmode.sh
Icon=usbmode
Terminal=false
Categories=applications;
EOF

  mksquashfs "$stage" "$out" -all-root -noappend -no-exports -no-xattrs -comp gzip >/dev/null
  echo "built $out ($(wc -c < "$out") bytes)"
}

build_variant USB "Switch to USB" "Reboot into USB storage mode"
build_variant ADB "Switch to ADB" "Reboot into ADB mode"

case "${1:-}" in
  --build-only) exit 0 ;;
esac

# Which entry is visible depends on the mode the Nano is in now.
MODE="$("$REMOTE" run 'if [ -d /sys/kernel/config/usb_gadget/FunKey/functions/ffs.adb ]; then echo adb; else echo storage; fi')"
if [ "$MODE" = adb ]; then
  SHOW=USB; HIDE=ADB
else
  SHOW=ADB; HIDE=USB
fi
echo "Nano is in $MODE mode: showing \"Switch to $SHOW\""

"$REMOTE" run "rm -f $APPS/USBMode.opk $APPS/SwitchToUSB.opk $APPS/SwitchToUSB.opk.off $APPS/SwitchToADB.opk $APPS/SwitchToADB.opk.off"
"$REMOTE" push "$HERE/SwitchTo$SHOW.opk" "$APPS/SwitchTo$SHOW.opk"
"$REMOTE" push "$HERE/SwitchTo$HIDE.opk" "$APPS/SwitchTo$HIDE.opk.off"

if [ "${1:-}" = "--refresh" ]; then
  # The frontend supervisor relaunches gmenu2x, which rescans the OPKs.
  "$REMOTE" run 'pkill gmenu2x && echo "menu restarted" || echo "gmenu2x is not running (an app is open?); the entries appear the next time the menu starts"'
else
  echo "installed. Restart the menu to see it (re-run with --refresh, or reboot the Nano)."
fi
