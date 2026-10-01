#!/bin/bash
# usbmode/usbmode.sh: mode decision, marker flip, and the menu-entry swap -
# WITHOUT touching the real /mnt/adb, the real Applications folder, or rebooting.
# Marker, log and Applications folder are redirected to /tmp on the device and
# USBMODE_DRYRUN=1 skips the reboot. USBMODE_QUIET=1 keeps the Nano's screen
# silent: its "SWITCHING TO ..." notice looks exactly like a real mode switch.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_adb_mode

WORK="$(native_tmpdir)"
APPS=/tmp/um_apps
ENV_NOISY="USBMODE_DRYRUN=1 USBMODE_MARKER=/tmp/um_marker USBMODE_LOG=/tmp/um_log USBMODE_APPS=$APPS"
ENV="$ENV_NOISY USBMODE_QUIET=1"
ls_apps() { "$R" run "ls $APPS 2>&1 | tr '\n' ' '"; }
reset_apps() { "$R" run "rm -rf $APPS; mkdir -p $APPS; cd $APPS; for f in $*; do : > \$f; done"; }
run_um() { "$R" run "$1 sh /tmp/usbmode_test.sh"; }

REAL_BEFORE="$("$R" run 'ls -l /mnt/adb 2>&1; ls /mnt/last_opk /run/rebooting 2>&1; ls /mnt/Applications | grep -i -E "usb|switch" | tr "\n" " "')"
"$R" push-text "$ROOT/usbmode/usbmode.sh" /tmp/usbmode_test.sh 755 2>/dev/null
"$R" run 'rm -f /tmp/um_marker /tmp/um_log'

echo "== A. ADB mode (real gadget) -> storage: marker removed, menu shows 'Switch to ADB' =="
reset_apps SwitchToUSB.opk SwitchToADB.opk.off
"$R" run 'touch /tmp/um_marker'
run_um "$ENV"; check "exit status" 0 $?
check "marker removed" gone "$("$R" run '[ -e /tmp/um_marker ] && echo present || echo gone')"
check "now shows ADB, USB parked" "SwitchToADB.opk SwitchToUSB.opk.off " "$(ls_apps)"
check "logged adb -> storage" "adb -> storage" "$("$R" run 'sed "s/^[^ ]* [^ ]* //" /tmp/um_log | tail -1')"

echo "== B. storage mode (no ffs.adb) -> ADB: marker created, menu shows 'Switch to USB' =="
run_um "USBMODE_GADGET=/nonexistent $ENV"; check "exit status" 0 $?
check "marker created" present "$("$R" run '[ -e /tmp/um_marker ] && echo present || echo gone')"
check "now shows USB, ADB parked" "SwitchToADB.opk.off SwitchToUSB.opk " "$(ls_apps)"
check "logged storage -> adb" "storage -> adb" "$("$R" run 'sed "s/^[^ ]* [^ ]* //" /tmp/um_log | tail -1')"

echo "== C. half-finished switch (marker gone, gadget still ADB): still ends in storage, no flip back =="
"$R" run 'rm -f /tmp/um_marker'
run_um "$ENV"; check "exit status" 0 $?
check "marker still gone" gone "$("$R" run '[ -e /tmp/um_marker ] && echo present || echo gone')"
check "menu shows ADB" "SwitchToADB.opk SwitchToUSB.opk.off " "$(ls_apps)"

echo "== D. marker cannot be written -> error, nonzero exit, menu NOT swapped =="
reset_apps SwitchToUSB.opk SwitchToADB.opk.off
"$R" run "USBMODE_QUIET=1 USBMODE_DRYRUN=1 USBMODE_GADGET=/nonexistent USBMODE_MARKER=/nonexistent/dir/adb USBMODE_LOG=/tmp/um_log USBMODE_APPS=$APPS sh /tmp/usbmode_test.sh"
check "exit status 1" 1 $?
check "menu untouched" "SwitchToADB.opk.off SwitchToUSB.opk " "$(ls_apps)"
check "logged FAILED" 1 "$("$R" run 'grep -c FAILED /tmp/um_log')"

echo "== E. self-healing and safety of the swap =="
reset_apps SwitchToUSB.opk SwitchToADB.opk                       # both visible (bad state)
"$R" run 'touch /tmp/um_marker'; run_um "$ENV"
check "both visible -> exactly one (ADB) visible" "SwitchToADB.opk SwitchToUSB.opk.off " "$(ls_apps)"
reset_apps SwitchToUSB.opk                                        # partner OPK missing
"$R" run 'touch /tmp/um_marker'; run_um "$ENV"; check "exit status with missing partner" 0 $?
check "missing partner: entry is NOT hidden (menu never empty)" "SwitchToUSB.opk " "$(ls_apps)"
reset_apps SwitchToADB.opk SwitchToUSB.opk.off                    # already in the desired state
"$R" run 'touch /tmp/um_marker'; run_um "$ENV"
check "already correct -> unchanged" "SwitchToADB.opk SwitchToUSB.opk.off " "$(ls_apps)"

echo "== G. quiet mode never draws a notice, normal mode does (stand-in notif: nothing appears on the screen) =="
printf '#!/bin/sh\necho "$*" >> /tmp/fake_notif.log\n' > "$WORK/notif"
"$R" push-text "$WORK/notif" /tmp/fakebin/notif 755 2>/dev/null
"$R" run 'rm -f /tmp/fake_notif.log'
reset_apps SwitchToUSB.opk SwitchToADB.opk.off
"$R" run 'touch /tmp/um_marker'
"$R" run "PATH=/tmp/fakebin:\$PATH $ENV sh /tmp/usbmode_test.sh"
check "quiet: notif was never called" "0" "$("$R" run 'cat /tmp/fake_notif.log 2>/dev/null | wc -l')"
"$R" run "PATH=/tmp/fakebin:\$PATH $ENV_NOISY sh /tmp/usbmode_test.sh"
has "not quiet: the notice is requested" "SWITCHING TO" "$("$R" run 'cat /tmp/fake_notif.log 2>/dev/null')"

echo "== F. real state untouched =="
REAL_AFTER="$("$R" run 'ls -l /mnt/adb 2>&1; ls /mnt/last_opk /run/rebooting 2>&1; ls /mnt/Applications | grep -i -E "usb|switch" | tr "\n" " "')"
check "real /mnt/adb, flags and Applications entries unchanged" "$REAL_BEFORE" "$REAL_AFTER"
"$R" run "rm -rf $APPS /tmp/um_marker /tmp/um_log /tmp/usbmode_test.sh /tmp/fakebin /tmp/fake_notif.log" >/dev/null
rm -rf "$WORK"

finish
