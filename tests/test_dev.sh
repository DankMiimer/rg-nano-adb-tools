#!/bin/bash
# shot, restart-menu and dev. Screenshots are decoded and their pixels checked
# (needs python3), not eyeballed. The screen turns red and blue for a few seconds.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_adb_mode
have_python || { echo "these tests decode PNGs and need python3"; exit 2; }

WORK="$(native_tmpdir)"
SHOTS="$(drive_tmpdir)"
export NANO_SHOT_DIR="$SHOTS"

echo "== 1. shot =="
P="$("$R" shot 2>/dev/null)"
[ -f "$P" ] && ok "default shot saved: $(basename "$P")" || bad "default shot file" "$P"
set -- $(png_center "$P"); check "screenshot is 240x240" "240 240" "$1 $2"
P2="$("$R" shot "$WORK/explicit.png" 2>/dev/null)"
check "explicit path (Linux filesystem) is honoured and printed" "$WORK/explicit.png" "$P2"
[ -s "$WORK/explicit.png" ] && ok "explicit shot has content" || bad "explicit shot empty"
check "no stray temp files on the device" "0" "$("$R" run 'ls /tmp/nano_shot_* 2>/dev/null | wc -l')"

echo "== 2. restart-menu =="
B="$(menu_pid)"
"$R" restart-menu >/dev/null 2>&1; check "exit status" 0 $?
sleep 4
A="$(menu_pid)"
[ -n "$A" ] && [ "$A" != "$B" ] && ok "menu relaunched with a new pid ($B -> $A)" || bad "menu pid" "before=$B after=$A"

echo "== 3. dev: local script, args, CRLF, shot, exit status, menu paused then restored =="
cat > "$WORK/redscreen.sh" <<'SH'
#!/bin/sh
echo "app: starting args=[$*]"
printf '\000\370' > /tmp/px
i=0; while [ $i -lt 17 ]; do cat /tmp/px /tmp/px > /tmp/px2; mv /tmp/px2 /tmp/px; i=$((i+1)); done
head -c 115200 /tmp/px > /dev/fb0
echo "app: screen is red"
sleep "$1"
echo "app: done"
exit 3
SH
sed -i 's/$/\r/' "$WORK/redscreen.sh"          # Windows line endings on purpose
B="$(menu_pid)"
"$R" dev --shot 3 "$WORK/redscreen.sh" 8 extra > "$WORK/dev.out" 2>&1 &
DEVPID=$!
sleep 5
check "menu is PAUSED (SIGSTOP) while the app runs" "State: T (stopped)" "$(menu_state)"
wait $DEVPID; rc=$?
OUT="$(cat "$WORK/dev.out")"
check "exit status is the app's (3)" 3 $rc
has "args passed to the app" "app: starting args=[8 extra]" "$OUT"
has "app output streamed" "app: screen is red" "$OUT"
has "app ran to the end" "app: done" "$OUT"
has "runner reports the status" "app exited with status 3" "$OUT"
SHOTP="$(grep -o "$SHOTS/shot_[^ ]*\.png" "$WORK/dev.out" | head -1)"
if [ -f "$SHOTP" ]; then
  ok "--shot 3 saved a screenshot"
  set -- $(png_center "$SHOTP")
  [ "$3" -gt 200 ] && [ "$4" -lt 60 ] && [ "$5" -lt 60 ] && ok "screenshot taken DURING the run is red (R=$3 G=$4 B=$5)" || bad "screenshot colour" "R=$3 G=$4 B=$5"
else
  bad "--shot file" "$OUT"
fi
check "CRLF stripped from the pushed script" "0" "$("$R" run "head -1 /tmp/nano_dev/redscreen.sh | od -c | grep -c '\\\\r'")"
sleep 3
A="$(menu_pid)"
[ -n "$A" ] && [ "$A" != "$B" ] && ok "menu restarted after the run ($B -> $A)" || bad "menu not restarted" "before=$B after=$A"
case "$(menu_state)" in *"(stopped)"*) bad "menu still stopped" ;; *) ok "menu is running (not stopped)" ;; esac

echo "== 4. dev --timeout kills a hung app and restores the menu =="
B="$(menu_pid)"; t0=$(date +%s)
OUT="$("$R" dev --timeout 3 sleep 100 2>&1)"; rc=$?
el=$(( $(date +%s) - t0 ))
[ $rc -ne 0 ] && ok "nonzero exit ($rc)" || bad "exit status should be nonzero"
has "timeout reported" "killed after the 3s timeout" "$OUT"
[ $el -lt 25 ] && ok "returned in ${el}s, not 100s" || bad "took too long" "${el}s"
sleep 3; A="$(menu_pid)"
[ -n "$A" ] && [ "$A" != "$B" ] && ok "menu restored after timeout" || bad "menu after timeout" "before=$B after=$A"

echo "== 5. dev refuses when no menu is running; --force runs without touching anything =="
B="$(menu_pid)"
OUT="$(NANO_DEV_FRONTENDS=nonexistent "$R" dev "$WORK/redscreen.sh" 1 2>&1)"; rc=$?
check "refused with status 3" 3 $rc
has "clear refusal message" "Refusing; use --force" "$OUT"
case "$OUT" in *"app: starting"*) bad "app must NOT have run" ;; *) ok "app did not run" ;; esac
OUT="$(NANO_DEV_FRONTENDS=nonexistent "$R" dev --force "$WORK/redscreen.sh" 1 2>&1)"; rc=$?
check "--force runs the app (its status 3)" 3 $rc
has "--force output" "app: done" "$OUT"
[ "$(menu_pid)" = "$B" ] && ok "menu pid unchanged ($B)" || bad "menu pid changed"
case "$(menu_state)" in *"(stopped)"*) bad "menu got stopped" ;; *) ok "menu not stopped" ;; esac

echo "== 6. dev with an .opk (run through opkrun like the menu does) =="
command -v mksquashfs >/dev/null 2>&1 || { echo "  SKIP  mksquashfs not installed (apt install squashfs-tools)"; SKIP_OPK=1; }
if [ -z "${SKIP_OPK:-}" ]; then
  mkdir -p "$WORK/opk"
  cat > "$WORK/opk/blue.funkey-s.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Blue test
Exec=run.sh
Terminal=false
Categories=applications;
EOF
  cat > "$WORK/opk/run.sh" <<'SH'
#!/bin/sh
echo "opk: running args=[$*]"
printf '\037\000' > /tmp/px
i=0; while [ $i -lt 17 ]; do cat /tmp/px /tmp/px > /tmp/px2; mv /tmp/px2 /tmp/px; i=$((i+1)); done
head -c 115200 /tmp/px > /dev/fb0
sleep 6
echo "opk: done"
SH
  chmod +x "$WORK/opk/run.sh"
  mksquashfs "$WORK/opk" "$WORK/blue.opk" -all-root -noappend -no-exports -no-xattrs -comp gzip >/dev/null
  B="$(menu_pid)"
  "$R" dev --shot 3 "$WORK/blue.opk" > "$WORK/opk.out" 2>&1; rc=$?
  OUT="$(cat "$WORK/opk.out")"
  check "opk exit status 0" 0 $rc
  has "opk output streamed" "opk: done" "$OUT"
  SHOTP="$(grep -o "$SHOTS/shot_[^ ]*\.png" "$WORK/opk.out" | head -1)"
  set -- $(png_center "$SHOTP")
  [ "$5" -gt 200 ] && [ "$3" -lt 60 ] && [ "$4" -lt 60 ] && ok "opk screen is blue during the run (R=$3 G=$4 B=$5)" || bad "opk screenshot colour" "R=$3 G=$4 B=$5 :: $OUT"
  check "/opk unmounted afterwards" "0" "$("$R" run 'mount | grep -c " /opk "')"
  sleep 3; A="$(menu_pid)"; [ -n "$A" ] && [ "$A" != "$B" ] && ok "menu restored after opk" || bad "menu after opk"
fi

echo "== 7. cleanup =="
"$R" run 'rm -rf /tmp/nano_dev /tmp/px /tmp/px2 /tmp/nano_dev.timeout' >/dev/null
rm -rf "$WORK" "$SHOTS"

finish
