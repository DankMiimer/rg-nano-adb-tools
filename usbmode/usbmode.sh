#!/bin/sh
# USB Mode - toggle the Nano's USB personality between ADB and mass storage.
#
# usb_gadget (run by S10share) only reads /mnt/adb while it builds the USB
# gadget at boot, so a switch is: flip the marker, reboot. With /mnt/adb the Nano
# comes up as an ADB device (and charges); without it, as the usual
# "FunKey S Shared Disk" (use Mount USB in the menu to share the card).
#
# The current mode is taken from what the running gadget really exposes, not
# from the marker, so a half-finished switch (marker flipped, not yet rebooted)
# simply finishes instead of flipping back.
#
# Test hooks (all optional):
#   USBMODE_GADGET   gadget dir to inspect   (default /sys/kernel/config/usb_gadget/FunKey)
#   USBMODE_MARKER   marker file to flip     (default /mnt/adb)
#   USBMODE_LOG      log file                (default /mnt/FunKey/usbmode.log)
#   USBMODE_APPS     menu folder with the OPKs (default /mnt/Applications)
#   USBMODE_DRYRUN=1 flip the marker and show the notice, but do not reboot
#   USBMODE_QUIET=1  show no on-screen notices (the tests use this: a "SWITCHING
#                    TO ..." notice on the Nano looks exactly like a real switch)
#
# Two OPKs ship, "Switch to USB" (SwitchToUSB.opk) and "Switch to ADB"
# (SwitchToADB.opk). Only one is visible in the menu: the one that matches what
# pressing it will do from the mode the Nano will be in after the reboot.

GADGET="${USBMODE_GADGET:-/sys/kernel/config/usb_gadget/FunKey}"
MARKER="${USBMODE_MARKER:-/mnt/adb}"
LOG="${USBMODE_LOG:-/mnt/FunKey/usbmode.log}"
APPS="${USBMODE_APPS:-/mnt/Applications}"

# Make the menu entry match the mode we are about to boot into: in ADB mode it
# says "Switch to USB", in storage mode "Switch to ADB". The inactive entry is
# parked as *.opk.off, which the menu does not scan. Renames only - the OPK that
# is running right now is loop-mounted, so its file must never be rewritten -
# and the new entry is revealed before the old one is hidden, so the menu is
# never left without one. Does nothing if the partner OPK is missing.
swap_labels() {
    if [ "$1" = storage ]; then
        SHOW=SwitchToADB; HIDE=SwitchToUSB
    else
        SHOW=SwitchToUSB; HIDE=SwitchToADB
    fi
    if [ -e "$APPS/$SHOW.opk" ] || [ -e "$APPS/$SHOW.opk.off" ]; then
        [ -e "$APPS/$SHOW.opk.off" ] && mv -f "$APPS/$SHOW.opk.off" "$APPS/$SHOW.opk"
        [ -e "$APPS/$HIDE.opk" ] && mv -f "$APPS/$HIDE.opk" "$APPS/$HIDE.opk.off"
    fi
}

if [ -d "$GADGET/functions/ffs.adb" ]; then
    CUR=adb
    NEXT=storage
else
    CUR=storage
    NEXT=adb
fi

if [ "$NEXT" = adb ]; then
    touch "$MARKER" 2>/dev/null
    [ -e "$MARKER" ] && DONE=1
else
    rm -f "$MARKER" 2>/dev/null
    [ ! -e "$MARKER" ] && DONE=1
fi

if [ -z "$DONE" ]; then
    echo "$(date '+%F %T') $CUR -> $NEXT FAILED (cannot write $MARKER)" >> "$LOG" 2>/dev/null
    if [ -z "$USBMODE_QUIET" ]; then
        notif set 4 "^^^^^^^^     USB MODE: ERROR^^   CANNOT WRITE /mnt^^^^^^^"
        sleep 4
    fi
    exit 1
fi

swap_labels "$NEXT"

echo "$(date '+%F %T') $CUR -> $NEXT" >> "$LOG" 2>/dev/null

# ^ is a line break and the leading spaces centre the text (same convention as
# the stock powerdown/update notices); 30 columns on the 240px screen.
if [ -z "$USBMODE_QUIET" ]; then
    if [ "$NEXT" = adb ]; then
        notif set 0 "^^^^^^^       SWITCHING TO ADB^         REBOOTING...^^^^^^^"
    else
        notif set 0 "^^^^^^^     SWITCHING TO STORAGE^         REBOOTING...^^^^^^^"
    fi
fi

sync

if [ -n "$USBMODE_DRYRUN" ]; then
    if [ -z "$USBMODE_QUIET" ]; then
        sleep 4
        notif clear
    fi
    exit 0
fi

# Do NOT touch /run/rebooting: from the frontend script, together with
# /mnt/last_opk it appears to make the frontend resume this app on the next boot,
# which would flip the mode again in a loop (inferred, not tested). The stock
# Reboot entry is just `reboot`; clearing last_opk is belt and braces.
rm -f /mnt/last_opk
sleep 3
sync
reboot
