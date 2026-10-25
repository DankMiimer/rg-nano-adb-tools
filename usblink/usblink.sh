#!/bin/sh
# USB Link - reconnect the Nano's USB without a reboot, optionally with a
# network link.
#
# The Nano only talks to a USB host that was plugged in when its USB gadget was
# set up at boot: unplug and replug the cable and ADB is gone until a reboot.
# Rebuilding the gadget while the cable is in brings it back. This rebuilds it
# the way /usr/local/sbin/adb does at boot (fresh FunctionFS, adbd started and
# ready before the gadget is bound), so it works with the stock firmware.
#
# By default the rebuilt gadget is the stock one, ADB + the serial console.
# NETWORK=ncm or NETWORK=rndis replaces the serial console with a network link:
# the musb controller has 4 data endpoints each way and serial + ADB + network
# would need 5 IN. The network function takes interfaces 0-1 like the serial
# console did, so ADB stays interface 2 and keeps its PC driver.
#   ncm    for Linux hosts. Windows' own NCM driver can't talk to this kernel's
#          NCM gadget (it logs "Wrong NDP SIGN" for every packet).
#   rndis  for Windows, with Microsoft OS descriptors so Windows loads its RNDIS
#          driver by itself. Windows only asks for those the first time it sees
#          a given USB ID and device version, and it has seen the stock gadget
#          without them, so this mode reports device version 0x0415 (stock 0x0414).
# The Nano gets ADDRESS on the link and, if authorized_keys is present, an SSH
# server (dropbear, key logins only, on that address only).
#
# Run it from the menu. Started over adb it kills its own adb session, so start
# it detached there (setsid sh usblink.sh </dev/null >/dev/null 2>&1 &).
#
# Settings, all optional, in $DIR/usblink.conf (KEY=value lines):
#   NETWORK=no|ncm|rndis    no (default): ADB + serial console, no link
#   ADDRESS=a.b.c.d/nn      the Nano's address on the link; default
#                           10.170.0.5/24 for ncm, 169.254.170.5/16 for rndis
#                           (Windows gives the adapter a 169.254 address itself)
#   SSH=auto|no             auto: start SSH if $DIR/authorized_keys exists
#
# Test hooks (all optional):
#   USBLINK_GADGET     gadget dir      (default /sys/kernel/config/usb_gadget/FunKey)
#   USBLINK_UDC_CLASS  UDC class dir   (default /sys/class/udc)
#   USBLINK_DIR        settings/keys   (default /mnt/USB-link)
#   USBLINK_LOG        log file        (default /mnt/FunKey/usblink.log)
#   USBLINK_POWER      USB power supply (default /sys/class/power_supply/axp20x-usb)
#   USBLINK_DRYRUN=1   print and log the plan, do not touch USB or the network
#   USBLINK_QUIET=1    show no on-screen notices

G="${USBLINK_GADGET:-/sys/kernel/config/usb_gadget/FunKey}"
UDCS="${USBLINK_UDC_CLASS:-/sys/class/udc}"
DIR="${USBLINK_DIR:-/mnt/USB-link}"
LOG="${USBLINK_LOG:-/mnt/FunKey/usblink.log}"
POWER="${USBLINK_POWER:-/sys/class/power_supply/axp20x-usb}"
C="$G/configs/FunKey.1"
FFS=/dev/usb-ffs/adb
DROPBEAR_PID=/tmp/usblink-dropbear.pid

log() { echo "$(date '+%F %T') $*" >> "$LOG" 2>/dev/null; sync; }

# conf KEY: value of KEY in usblink.conf (CRs and quotes dropped)
conf() { sed -n "s/^$1=//p" "$DIR/usblink.conf" 2>/dev/null | tail -n 1 | tr -d '\r"'; }

# center TEXT: pad TEXT to the middle of the 30-column screen
center() {
    n=$(( (30 - ${#1}) / 2 ))
    p=""
    while [ "$n" -gt 0 ]; do p="$p "; n=$((n - 1)); done
    printf '%s%s' "$p" "$1"
}

# notice SECONDS LINE...: centred notice, ^ = line break (0 = until cleared)
notice() {
    [ -n "$USBLINK_QUIET" ] && return
    t="$1"; shift
    m="^^^^^^"
    for l in "$@"; do m="$m$(center "$l")^"; done
    notif set "$t" "$m^^^^^"
}

state() { cat "$UDCS/$UDC/state" 2>/dev/null; }

NETWORK="$(conf NETWORK)"
case "$NETWORK" in
    ncm|rndis) KIND="$NETWORK" ;;
    ""|no) KIND=acm ;;
    *) KIND=acm; log "unknown NETWORK=$NETWORK, using no" ;;
esac
ADDRESS="$(conf ADDRESS)"
if [ -z "$ADDRESS" ]; then
    case "$KIND" in
        ncm) ADDRESS=10.170.0.5/24 ;;
        rndis) ADDRESS=169.254.170.5/16 ;;
        *) ADDRESS=none ;;
    esac
fi
IP="${ADDRESS%/*}"
SSH="$(conf SSH)"; SSH="${SSH:-auto}"

if [ ! -d "$G/functions/ffs.adb" ]; then
    log "not in ADB mode, nothing done"
    notice 4 "USB LINK: NOT IN ADB MODE" "SWITCH TO ADB FIRST"
    [ -z "$USBLINK_QUIET" ] && sleep 4
    exit 1
fi

UDC="$(ls "$UDCS" 2>/dev/null | head -n 1)"
SERIAL="$(sed -n 's/^Serial[[:space:]]*: *//p' /proc/cpuinfo)"
# Fixed, locally administered MACs from the CPU serial, so the PC sees the
# same network adapter every time
SUF="$(printf '%s' "$SERIAL" | tail -c 10 | sed 's/\(..\)/:\1/g')"
MAC_DEV="02$SUF"
MAC_HOST="12$SUF"

SSHON=0
if [ "$KIND" != acm ] && [ "$SSH" != no ] && [ -s "$DIR/authorized_keys" ]; then SSHON=1; fi

PLAN="gadget=$KIND address=$ADDRESS ssh=$SSHON mac_dev=$MAC_DEV mac_host=$MAC_HOST"
log "start: udc $UDC state $(state), usb power $(cat "$POWER/online" 2>/dev/null), $PLAN"

if [ -n "$USBLINK_DRYRUN" ]; then
    echo "plan: $PLAN"
    log "dry run, nothing changed"
    notice 3 "USB LINK: DRY RUN"
    exit 0
fi

notice 0 "RECONNECTING USB..."

# Tear down the running gadget and adbd. The function filesystem must be fresh:
# changing the gadget under a running adbd leaves USB dead until a reboot.
echo "" > "$G/UDC" 2>/dev/null
killall adbd 2>/dev/null
sleep 1
umount "$FFS" 2>/dev/null
for l in "$C/acm.GS0" "$C/ffs.adb" "$C/ncm.usb0" "$C/rndis.usb0" "$G/os_desc/FunKey.1"; do
    [ -L "$l" ] && rm "$l"
done
rmdir "$C/strings/0x409" "$C" "$G/functions/acm.GS0" "$G/functions/ffs.adb" \
      "$G/functions/ncm.usb0" "$G/functions/rndis.usb0" "$G/strings/0x409" "$G" 2>/dev/null

# Build it again: same IDs and strings as the stock ADB gadget
modprobe libcomposite
modprobe usb_f_fs
mkdir "$G" "$G/strings/0x409" "$C" "$C/strings/0x409"
case "$KIND" in
    ncm|rndis)
        FUNC="$KIND.usb0"
        modprobe "usb_f_$KIND"
        mkdir "$G/functions/$FUNC"
        echo "$MAC_DEV" > "$G/functions/$FUNC/dev_addr"
        echo "$MAC_HOST" > "$G/functions/$FUNC/host_addr"
        NET="$(echo "$KIND" | tr 'a-z' 'A-Z')"
        CFG="ADB+$NET"
        ;;
    *)
        FUNC=acm.GS0
        modprobe usb_f_serial
        modprobe usb_f_acm
        mkdir "$G/functions/$FUNC"
        CFG="ADB+CDC"
        ;;
esac
mkdir "$G/functions/ffs.adb"
echo 0x8087 > "$G/idVendor"
echo 0x011e > "$G/idProduct"
echo "Intel" > "$G/strings/0x409/manufacturer"
echo "FunKey S" > "$G/strings/0x409/product"
echo "$SERIAL" > "$G/strings/0x409/serialnumber"
echo 120 > "$C/MaxPower"
echo "$CFG" > "$C/strings/0x409/configuration"
if [ "$KIND" = rndis ]; then
    # What usb_gadget does for its RNDIS mode, so Windows binds its RNDIS driver
    echo 0x0415 > "$G/bcdDevice"
    echo 1 > "$G/os_desc/use"
    echo 0xcd > "$G/os_desc/b_vendor_code"
    echo MSFT100 > "$G/os_desc/qw_sign"
    echo RNDIS > "$G/functions/$FUNC/os_desc/interface.rndis/compatible_id"
    echo 5162001 > "$G/functions/$FUNC/os_desc/interface.rndis/sub_compatible_id"
    ln -s "$C" "$G/os_desc"
fi
# Network (or serial) first, so ADB is interface 2 as in the stock gadget
ln -s "$G/functions/$FUNC" "$C/"
ln -s "$G/functions/ffs.adb" "$C/"

mkdir -p "$FFS"
mount -t functionfs adb "$FFS"
setsid adbd </dev/null >/dev/null 2>&1 &
# adbd is ready once it has written its descriptors and the endpoints exist
i=0
while [ ! -e "$FFS/ep1" ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
sleep 1
if ! echo "$UDC" > "$G/UDC"; then
    log "bind failed: $(dmesg | tail -n 2 | tr '\n' ' ')"
fi

# Wait for the host to configure it, logging what the Nano sees (USB power,
# its voltage, gadget state) whenever it changes: that is all there is to go
# on when a host does not connect
i=0
last=""
while [ $i -le 15 ]; do
    v="$(cat "$POWER/voltage_now" 2>/dev/null || echo 0)"
    s="state $(state), usb power $(cat "$POWER/online" 2>/dev/null) at $((v / 1000000)).$((v / 100000 % 10)) V"
    [ "$s" != "$last" ] && log "  ${i}s: $s" && last="$s"
    [ "$(state)" = configured ] && break
    sleep 1
    i=$((i + 1))
done
CONNECTED=0
[ "$(state)" = configured ] && CONNECTED=1

if [ -f "$DROPBEAR_PID" ]; then kill "$(cat "$DROPBEAR_PID")" 2>/dev/null; rm -f "$DROPBEAR_PID"; fi
if [ "$KIND" != acm ]; then
    IF="$(cat "$G/functions/$FUNC/ifname")"
    ip addr flush dev "$IF"
    ip addr add "$ADDRESS" dev "$IF"
    ip link set "$IF" up
    if [ "$SSHON" = 1 ]; then
        # /root is read-only and dropbear only reads ~/.ssh/authorized_keys, so
        # /root becomes a small RAM copy of itself with the key added
        if ! touch /root/.usblink 2>/dev/null; then
            rm -rf /tmp/usblink-root
            mkdir -p /tmp/usblink-root
            cp -a /root/. /tmp/usblink-root/
            mount -t tmpfs -o size=256k,mode=0755 tmpfs /root
            cp -a /tmp/usblink-root/. /root/
            rm -rf /tmp/usblink-root
        fi
        rm -f /root/.usblink
        mkdir -p /root/.ssh
        tr -d '\r' < "$DIR/authorized_keys" > /root/.ssh/authorized_keys
        chmod 700 /root/.ssh
        chmod 600 /root/.ssh/authorized_keys
        [ -s "$DIR/dropbear_ed25519_host_key" ] || dropbearkey -t ed25519 -f "$DIR/dropbear_ed25519_host_key" >/dev/null 2>&1
        cp "$DIR/dropbear_ed25519_host_key" /tmp/usblink-hostkey
        chmod 600 /tmp/usblink-hostkey
        dropbear -s -r /tmp/usblink-hostkey -p "$IP:22" -P "$DROPBEAR_PID"
    fi
fi

log "done: state $(state), connected $CONNECTED"
[ -z "$USBLINK_QUIET" ] && notif clear
if [ "$CONNECTED" = 1 ]; then
    if [ "$KIND" = acm ]; then
        notice 3 "USB LINK: CONNECTED" "ADB"
    else
        notice 3 "USB LINK: CONNECTED" "ADB + NETWORK ($NET)" "$IP"
    fi
else
    notice 4 "USB LINK: NO HOST FOUND" "PLUG THE CABLE IN" "AND TRY AGAIN"
fi
[ -z "$USBLINK_QUIET" ] && sleep 3
exit 0
