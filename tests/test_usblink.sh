#!/bin/bash
# usblink/usblink.sh: mode check, settings, SSH decision, MACs and notices -
# WITHOUT touching the real USB gadget (that would drop this adb session).
# USBLINK_DRYRUN=1 stops before the gadget is touched; settings, keys and log
# are redirected to /tmp on the device. USBLINK_QUIET=1 keeps the screen silent.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_adb_mode

WORK="$(native_tmpdir)"
D=/tmp/ul_dir
ENV_NOISY="USBLINK_DRYRUN=1 USBLINK_DIR=$D USBLINK_LOG=/tmp/ul_log"
ENV="$ENV_NOISY USBLINK_QUIET=1"
run_ul() { "$R" run "$1 sh /tmp/usblink_test.sh"; }
plan() { run_ul "$ENV" | sed -n 's/^plan: //p'; }
field() { printf '%s\n' "$2" | tr ' ' '\n' | sed -n "s/^$1=//p"; }
settings() { "$R" run "rm -rf $D; mkdir -p $D; printf '$1' > $D/usblink.conf"; }

REAL_BEFORE="$("$R" run 'G=/sys/kernel/config/usb_gadget/FunKey; ls $G/configs/FunKey.1 | tr "\n" " "; cat $G/UDC; ls -l /mnt/FunKey/usblink.log 2>&1; ls /mnt/USB-link 2>&1 | tr "\n" " "')"
"$R" push-text "$ROOT/usblink/usblink.sh" /tmp/usblink_test.sh 755 2>/dev/null
"$R" run "rm -rf $D /tmp/ul_log; mkdir -p $D"

echo "== A. storage mode (no ffs.adb): refuses, exit 1, nothing else done =="
run_ul "USBLINK_GADGET=/nonexistent $ENV"; check "exit status 1" 1 $?
has "logged not in ADB mode" "not in ADB mode" "$("$R" run 'cat /tmp/ul_log')"

echo "== B. ADB mode, no settings: the stock ADB + serial gadget, no network, no SSH even with a key =="
run_ul "$ENV" >/dev/null; check "exit status" 0 $?
"$R" run "echo 'ssh-ed25519 AAAA test' > $D/authorized_keys"
P="$(plan)"
check "gadget" acm "$(field gadget "$P")"
check "address" none "$(field address "$P")"
check "no SSH without a network link" 0 "$(field ssh "$P")"
check "watch mode on by default" 1 "$(field watch "$P")"

echo "== C. NETWORK=ncm: default address 10.170.0.5/24, SSH only with authorized_keys =="
settings 'NETWORK=ncm\n'
P="$(plan)"
check "gadget" ncm "$(field gadget "$P")"
check "address" 10.170.0.5/24 "$(field address "$P")"
check "ssh off without authorized_keys" 0 "$(field ssh "$P")"
"$R" run "echo 'ssh-ed25519 AAAA test' > $D/authorized_keys"
check "ssh on with authorized_keys" 1 "$(field ssh "$(plan)")"

echo "== D. NETWORK=rndis: link-local default address; ADDRESS overrides it (CRLF and quotes in the file) =="
settings 'NETWORK=rndis\n'
P="$(plan)"
check "gadget" rndis "$(field gadget "$P")"
check "address" 169.254.170.5/16 "$(field address "$P")"
settings 'NETWORK="rndis"\r\nADDRESS="10.9.9.9/24"\r\n'
P="$(plan)"
check "gadget with CRLF and quotes" rndis "$(field gadget "$P")"
check "address override" 10.9.9.9/24 "$(field address "$P")"

echo "== E. SSH=no wins over authorized_keys; an unknown NETWORK value means no network =="
settings 'NETWORK=ncm\nSSH=no\n'
"$R" run "echo 'ssh-ed25519 AAAA test' > $D/authorized_keys"
P="$(plan)"
check "gadget" ncm "$(field gadget "$P")"
check "ssh off" 0 "$(field ssh "$P")"
settings 'NETWORK=yes\n'
check "NETWORK=yes falls back to the stock gadget" acm "$(field gadget "$(plan)")"
has "and says so in the log" "unknown NETWORK=yes" "$("$R" run 'cat /tmp/ul_log')"
settings 'WATCH=no\n'
check "WATCH=no turns watch mode off" 0 "$(field watch "$(plan)")"

echo "== W. the watcher, on fake status files (dry run: it logs rebuilds instead of doing them) =="
# Fake gadget, UDC state and USB power; 0.2 s ticks and 2 ticks of settling.
W=/tmp/ulw
WENV="USBLINK_DRYRUN=1 USBLINK_QUIET=1 USBLINK_TICK=0.2 USBLINK_SETTLE=2 USBLINK_GADGET=$W/g USBLINK_UDC_CLASS=$W/udc USBLINK_POWER=$W/pw USBLINK_RUN=$W/run USBLINK_DIR=$D USBLINK_LOG=$W/log"
"$R" run "rm -rf $W $D; mkdir -p $W/g/functions/ffs.adb $W/udc/fake.udc $W/pw $W/run $D; echo 'not attached' > $W/udc/fake.udc/state; echo 0 > $W/pw/online"
"$R" run "$WENV setsid sh /tmp/usblink_test.sh --watch </dev/null >/dev/null 2>&1 & sleep 1"
power() { "$R" run "echo $1 > $W/pw/online"; }
udc() { "$R" run "echo '$1' > $W/udc/fake.udc/state"; }
rebuilds() { "$R" run "grep -c 'nothing rebuilt' $W/log"; }
check "watcher is running" alive "$("$R" run "kill -0 \$(cat $W/run/usblink-watch.pid) 2>/dev/null && echo alive || echo dead")"
has "starting unplugged is noticed" "watch: unplugged" "$("$R" run "cat $W/log")"
power 1; sleep 2
check "plugged in, no host: one rebuild" 1 "$(rebuilds)"
sleep 2
check "still plugged in: no second rebuild (a charger can't make it loop)" 1 "$(rebuilds)"
power 0; sleep 1; power 1; sleep 2
check "replugged, no host: rebuilds again" 2 "$(rebuilds)"
power 0; sleep 1; udc configured; power 1; sleep 2
check "replugged and the host connects by itself: left alone" 2 "$(rebuilds)"
power 0; sleep 1; udc suspended; power 1; sleep 2
check "replugged, host has suspended it: left alone" 2 "$(rebuilds)"
udc configured; sleep 1; udc 'not attached'; sleep 2
check "host drops a working connection, power stays on: rebuilds" 3 "$(rebuilds)"
has "and says why" "host dropped the connection" "$("$R" run "cat $W/log")"
sleep 2
check "still no host after that: no second rebuild" 3 "$(rebuilds)"
udc configured; sleep 1; udc 'not attached'; sleep 2
check "connects again, then dropped again: one more rebuild" 4 "$(rebuilds)"
"$R" run "kill \$(cat $W/run/usblink-watch.pid)"; sleep 1
check "watcher stops when killed" dead "$("$R" run "kill -0 \$(cat $W/run/usblink-watch.pid) 2>/dev/null && echo alive || echo dead")"
"$R" run "rm -rf $W"

echo "== F. MAC addresses: fixed, locally administered, same suffix =="
P="$(plan)"
DEV="$(field mac_dev "$P")"; HOST="$(field mac_host "$P")"
check "device MAC starts 02:" "02:" "${DEV:0:3}"
check "host MAC starts 12:" "12:" "${HOST:0:3}"
check "6 octets" 6 "$(printf '%s' "$DEV" | tr ':' '\n' | grep -c .)"
check "same suffix" "${DEV:2}" "${HOST:2}"
check "same MACs on every run" "$DEV" "$(field mac_dev "$(plan)")"

echo "== G. quiet mode never draws a notice, normal mode does (stand-in notif: nothing appears on the screen) =="
printf '#!/bin/sh\necho "$*" >> /tmp/ul_fake_notif.log\n' > "$WORK/notif"
"$R" push-text "$WORK/notif" /tmp/ul_fakebin/notif 755 2>/dev/null
"$R" run 'rm -f /tmp/ul_fake_notif.log'
"$R" run "PATH=/tmp/ul_fakebin:\$PATH $ENV sh /tmp/usblink_test.sh" >/dev/null
"$R" run "PATH=/tmp/ul_fakebin:\$PATH USBLINK_GADGET=/nonexistent $ENV sh /tmp/usblink_test.sh" >/dev/null
check "quiet: notif was never called" "0" "$("$R" run 'cat /tmp/ul_fake_notif.log 2>/dev/null | wc -l')"
"$R" run "PATH=/tmp/ul_fakebin:\$PATH $ENV_NOISY sh /tmp/usblink_test.sh" >/dev/null
has "not quiet: the notice is requested" "DRY RUN" "$("$R" run 'cat /tmp/ul_fake_notif.log 2>/dev/null')"
"$R" run "PATH=/tmp/ul_fakebin:\$PATH USBLINK_GADGET=/nonexistent $ENV_NOISY sh /tmp/usblink_test.sh" >/dev/null
has "not quiet, storage mode: says so" "NOT IN ADB MODE" "$("$R" run 'cat /tmp/ul_fake_notif.log 2>/dev/null')"

echo "== H. real state untouched =="
REAL_AFTER="$("$R" run 'G=/sys/kernel/config/usb_gadget/FunKey; ls $G/configs/FunKey.1 | tr "\n" " "; cat $G/UDC; ls -l /mnt/FunKey/usblink.log 2>&1; ls /mnt/USB-link 2>&1 | tr "\n" " "')"
check "real gadget, USB Link log and settings folder unchanged" "$REAL_BEFORE" "$REAL_AFTER"
"$R" run "rm -rf $D /tmp/ul_log /tmp/usblink_test.sh /tmp/ul_fakebin /tmp/ul_fake_notif.log" >/dev/null
rm -rf "$WORK"

finish
