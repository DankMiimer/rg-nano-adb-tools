#!/bin/bash
# Builds the USB Link OPK and installs it in the Applications menu over adb.
#
#   ./build_usblink_opk.sh              build it and install it on the Nano
#   ./build_usblink_opk.sh --refresh    ...and restart gmenu2x so it shows now
#   ./build_usblink_opk.sh --build-only just build, do not touch the device
#
# Needs: bash, mksquashfs (squashfs-tools), python3, and nanoctl (for the push).
# The Nano must be in ADB mode to install; --build-only needs no device.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE="$HERE/../nanoctl"
APPS="/mnt/Applications"   # /media is a symlink to /mnt on the device
OUT="$HERE/USBLink.opk"

command -v mksquashfs >/dev/null || { echo "mksquashfs is required (apt install squashfs-tools)" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required to draw the icon" >&2; exit 1; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# CRLF-safe copy: the script runs under busybox sh on the Nano.
sed 's/\r$//' "$HERE/usblink.sh" > "$STAGE/usblink.sh"
chmod +x "$STAGE/usblink.sh"
python3 "$HERE/../usbmode/make_icon.py" link "$STAGE/usblink.png" >/dev/null

cat > "$STAGE/usblink.funkey-s.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=USB Link
Comment=Reconnect USB without a reboot
Exec=usblink.sh
Icon=usblink
Terminal=false
Categories=applications;
EOF

mksquashfs "$STAGE" "$OUT" -all-root -noappend -no-exports -no-xattrs -comp gzip >/dev/null
echo "built $OUT ($(wc -c < "$OUT") bytes)"

case "${1:-}" in
  --build-only) exit 0 ;;
esac

"$REMOTE" push "$OUT" "$APPS/USBLink.opk"

if [ "${1:-}" = "--refresh" ]; then
  # The frontend supervisor relaunches gmenu2x, which rescans the OPKs.
  "$REMOTE" run 'pkill gmenu2x && echo "menu restarted" || echo "gmenu2x is not running (an app is open?); the entry appears the next time the menu starts"'
else
  echo "installed. Restart the menu to see it (re-run with --refresh, or reboot the Nano)."
fi
