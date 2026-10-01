#!/bin/bash
# Shared helpers for the integration tests.
#
# These talk to a REAL Nano: it must be plugged in over USB, in ADB mode and
# sitting idle at the menu. They briefly fill the screen with colour, restart
# the menu and write only to /tmp and /mnt/nanoctl_test on the device (removed
# afterwards). Your real USB mode, menu entries and files are not touched.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
R="$ROOT/nanoctl"
PASS=0
FAIL=0

ok()    { PASS=$((PASS + 1)); echo "  PASS  $1"; }
bad()   { FAIL=$((FAIL + 1)); echo "  FAIL  $1${2:+  $2}"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$2] got [$3]"; fi; }
has()   { case "$3" in *"$2"*) ok "$1" ;; *) bad "$1" "missing [$2] in [$3]" ;; esac; }
finish() { echo; echo "RESULT: $PASS passed, $FAIL failed"; [ "$FAIL" -eq 0 ]; }

# A scratch dir that adb.exe can read directly. Under WSL that has to be on a
# Windows drive; elsewhere any temp dir will do.
drive_tmpdir() {
  local base="" t="" cmd=""
  cmd="$(command -v cmd.exe 2>/dev/null || true)"
  if [ -z "$cmd" ]; then
    for c in /mnt/[a-zA-Z]/Windows/System32/cmd.exe; do
      [ -x "$c" ] && cmd="$c" && break
    done
  fi
  if [ -n "$cmd" ] && command -v wslpath >/dev/null 2>&1; then
    t="$(cd "$(dirname "$cmd")" && "$cmd" /c 'echo %TEMP%' 2>/dev/null | tr -d '\r')"
    [ -z "$t" ] || base="$(wslpath -u "$t")"
  fi
  mktemp -d -p "${base:-${TMPDIR:-/tmp}}"
}

# A scratch dir on the Linux filesystem (under WSL this exercises the staging path).
native_tmpdir() { mktemp -d; }

require_adb_mode() {
  local m
  m="$("$R" run 'if [ -d /sys/kernel/config/usb_gadget/FunKey/functions/ffs.adb ]; then echo adb; else echo storage; fi' 2>&1)" \
    || { echo "tests need an adb device: $m"; exit 2; }
  [ "$m" = adb ] || { echo "tests need the Nano in ADB mode (it is in '$m' mode)"; exit 2; }
}

menu_pid() {
  "$R" run 'for p in /proc/[0-9]*; do [ "$(cat $p/comm 2>/dev/null)" = gmenu2x ] && echo ${p#/proc/}; done'
}

menu_state() {
  "$R" run 'for p in /proc/[0-9]*; do [ "$(cat $p/comm 2>/dev/null)" = gmenu2x ] && grep "^State" $p/status; done' | tr -s '\t ' ' '
}

# Prints "W H R G B" (centre pixel) of a PNG; needs python3.
png_center() { python3 "$ROOT/tests/png_center.py" "$1"; }
have_python() { command -v python3 >/dev/null 2>&1; }
