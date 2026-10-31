#!/bin/sh
# USB Link - reconnect the Nano's USB without a reboot, optionally with a
# network link, and keep reconnecting after every replug.
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
# If a network gadget won't bind, the stock gadget is built instead, so USB is
# never left dead. The Nano gets ADDRESS on the link and, if authorized_keys is
# present, an SSH server (dropbear, key logins only, on that address only).
# With ADDRESS=dhcp, or a GATEWAY, it also uses the host's internet connection:
# the host has to share it (Windows: Internet Connection Sharing, which also
# runs the DHCP server; Linux: NAT).
#
# Watch mode (WATCH=yes, the default): after a run, a watcher stays in the
# background until the next reboot. When USB power is present but no host has
# configured the gadget after a few seconds, it rebuilds the gadget, so a replug
# reconnects by itself, and so does a host that drops a working connection while
# the port stays powered. One rebuild per plug-in or lost connection: a charger,
# which powers the Nano but never configures it, gets one and no more. It never
# touches a configured (or suspended) gadget, so it can't interrupt a working
# connection. It runs from a
# copy in /tmp, because the menu unmounts the OPK when the app exits, and reads
# its two status files with the shell's own `read`, so it costs next to nothing.
#
# Run it from the menu. Started over adb it kills its own adb session, so start
# it detached there (setsid sh usblink.sh </dev/null >/dev/null 2>&1 &).
#
# Settings, all optional, in $DIR/usblink.conf (KEY=value lines):
#   NETWORK=no|ncm|rndis    no (default): ADB + serial console, no link
#   ADDRESS=a.b.c.d/nn|dhcp the Nano's address on the link; default
#                           10.170.0.5/24 for ncm, 169.254.170.5/16 for rndis
#                           (Windows gives the adapter a 169.254 address itself).
#                           dhcp: address, route and DNS from the host's DHCP
#                           server; the default address if none answers
#   GATEWAY=a.b.c.d         with a fixed ADDRESS: route to the internet via the
#                           host at this address (default: none)
#   DNS=a.b.c.d[,a.b.c.d]   name servers with GATEWAY (default: the GATEWAY) or
#                           with dhcp (default: the host's)
#   SSH=auto|no             auto: start SSH if $DIR/authorized_keys exists
#   WATCH=yes|no            yes (default): reconnect by itself after replugs
#
# Test hooks (all optional):
#   USBLINK_GADGET     gadget dir      (default /sys/kernel/config/usb_gadget/FunKey)
#   USBLINK_UDC_CLASS  UDC class dir   (default /sys/class/udc)
#   USBLINK_DIR        settings/keys   (default /mnt/USB-link)
#   USBLINK_LOG        log file        (default /mnt/FunKey/usblink.log)
#   USBLINK_POWER      USB power supply (default /sys/class/power_supply/axp20x-usb)
#   USBLINK_RUN        pid files, the DHCP lease and the watcher's copy (default /tmp)
#   USBLINK_RESOLV     DNS settings file (default /etc/resolv.conf)
#   USBLINK_TICK       watcher poll interval in seconds (default 1)
#   USBLINK_SETTLE     ticks the watcher gives a host before rebuilding (default 6)
#   USBLINK_DRYRUN=1   print and log the plan, do not touch USB or the network;
#                      with --watch, log the rebuilds instead of doing them
#   USBLINK_QUIET=1    show no on-screen notices
#   USBLINK_FORCE_FAIL=1  make a network gadget fail to bind (tests the fallback)
#   USBLINK_LINKTEST=IF   only set up the address, route and DNS on interface IF,
#                      print the result and exit (USB is not touched)

# udhcpc runs this script for its events (-s), with the lease in its
# environment: write the lease down for the run that started it, nothing else
if [ -n "$USBLINK_DHCP_OUT" ]; then
    # shellcheck disable=SC2154 # ip, mask, router, dns: set by udhcpc
    case "$1" in
        bound|renew) printf 'ip=%s\nmask=%s\nrouter=%s\ndns=%s\n' "$ip" "$mask" "$router" "$dns" > "$USBLINK_DHCP_OUT" ;;
    esac
    exit 0
fi

SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
G="${USBLINK_GADGET:-/sys/kernel/config/usb_gadget/FunKey}"
UDCS="${USBLINK_UDC_CLASS:-/sys/class/udc}"
DIR="${USBLINK_DIR:-/mnt/USB-link}"
LOG="${USBLINK_LOG:-/mnt/FunKey/usblink.log}"
POWER="${USBLINK_POWER:-/sys/class/power_supply/axp20x-usb}"
RUN="${USBLINK_RUN:-/tmp}"
RESOLV="${USBLINK_RESOLV:-/etc/resolv.conf}"
TICK="${USBLINK_TICK:-1}"
SETTLE="${USBLINK_SETTLE:-6}"
C="$G/configs/FunKey.1"
FFS=/dev/usb-ffs/adb
DROPBEAR_PID="$RUN/usblink-dropbear.pid"
WATCH_PID="$RUN/usblink-watch.pid"
WATCH_COPY="$RUN/usblink-watch.sh"
LEASE="$RUN/usblink-lease"

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

state() { s=""; read -r s < "$UDCS/$UDC/state" 2>/dev/null; echo "$s"; }

NETWORK="$(conf NETWORK)"
case "$NETWORK" in
    ncm|rndis) KIND="$NETWORK" ;;
    ""|no) KIND=acm ;;
    *) KIND=acm; log "unknown NETWORK=$NETWORK, using no" ;;
esac
case "$KIND" in
    ncm) DEFAULT_ADDRESS=10.170.0.5/24 ;;
    rndis) DEFAULT_ADDRESS=169.254.170.5/16 ;;
    *) DEFAULT_ADDRESS=none ;;
esac
ADDRESS="$(conf ADDRESS)"
if [ -z "$ADDRESS" ] || [ "$KIND" = acm ]; then ADDRESS="$DEFAULT_ADDRESS"; fi
GATEWAY="$(conf GATEWAY)"
DNS="$(conf DNS | tr ',' ' ')"
if [ "$KIND" = acm ]; then
    GATEWAY=none; DNS=none
elif [ "$ADDRESS" = dhcp ]; then
    GATEWAY=dhcp; DNS="${DNS:-dhcp}"
elif [ -z "$GATEWAY" ]; then
    GATEWAY=none; DNS=none
else
    DNS="${DNS:-$GATEWAY}"
fi
IP="${ADDRESS%/*}"
SSH="$(conf SSH)"; SSH="${SSH:-auto}"
case "$(conf WATCH)" in no) WATCH=0 ;; *) WATCH=1 ;; esac

# dotted ADDR...: true if every argument is a dotted IPv4 address
dotted() {
    [ $# -gt 0 ] || return 1
    for a in "$@"; do
        case "$a" in *[!0-9.]*|"") return 1 ;; esac
    done
}

# set_dns SERVER...: the name servers the Nano uses
set_dns() { for d in "$@"; do echo "nameserver $d"; done > "$RESOLV"; }

# dhcp IF: ask the host's DHCP server for an address, a route and DNS and set
# them on IF; returns 1 if no server answers
dhcp() {
    rm -f "$LEASE"
    USBLINK_DHCP_OUT="$LEASE" udhcpc -i "$1" -s "$SELF" -n -q -t 6 -T 2 >/dev/null 2>&1
    [ -s "$LEASE" ] || return 1
    l_ip=""; l_mask=""; l_router=""; l_dns=""
    while IFS='=' read -r k v; do
        case "$k" in
            ip) l_ip="$v" ;;
            mask) l_mask="$v" ;;
            router) l_router="${v%% *}" ;;
            dns) l_dns="$v" ;;
        esac
    done < "$LEASE"
    rm -f "$LEASE"
    # It comes from the network: use it only if it looks like addresses
    dotted "$l_ip" || { log "dhcp: unusable address [$l_ip]"; return 1; }
    case "$l_mask" in ""|*[!0-9]*) l_mask=24 ;; esac
    ip addr add "$l_ip/$l_mask" dev "$1"
    IP="$l_ip"
    if dotted "$l_router"; then
        ip route replace default via "$l_router" dev "$1"
        ROUTED=1
        # DNS from the settings wins: Windows' sharing DNS proxy answers
        # "no such name" to IPv6 lookups, and musl then gives up on the name
        # shellcheck disable=SC2086 # lists of servers
        if [ "$DNS" != dhcp ]; then used="$DNS"
        elif dotted $l_dns; then used="$l_dns"
        else used="$l_router"; fi
        # shellcheck disable=SC2086
        set_dns $used
    fi
    log "dhcp: $l_ip/$l_mask, route via ${l_router:-none}, dns ${used:-none} (the host offered ${l_dns:-none})"
}

# net_config IF: address, route and DNS on the network link IF
ROUTED=0; DHCP_FAILED=0
net_config() {
    ip addr flush dev "$1"
    ip link set "$1" up
    if [ "$ADDRESS" = dhcp ]; then
        dhcp "$1" && return
        log "dhcp: no answer from the host, using $DEFAULT_ADDRESS"
        DHCP_FAILED=1
        ip addr add "$DEFAULT_ADDRESS" dev "$1"
        IP="${DEFAULT_ADDRESS%/*}"
        return
    fi
    ip addr add "$ADDRESS" dev "$1"
    if [ "$GATEWAY" != none ]; then
        ip route replace default via "$GATEWAY" dev "$1"
        # shellcheck disable=SC2086 # a list of servers
        set_dns $DNS
        ROUTED=1
    fi
}

if [ -n "$USBLINK_LINKTEST" ]; then
    net_config "$USBLINK_LINKTEST"
    echo "link: ip=$IP routed=$ROUTED dhcp_failed=$DHCP_FAILED"
    exit 0
fi

if [ ! -d "$G/functions/ffs.adb" ]; then
    [ "$1" = --watch ] && exit 0
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

# Take the running gadget and adbd down completely. The function filesystem
# must be fresh: changing the gadget under a running adbd leaves USB dead until
# a reboot.
teardown() {
    echo "" > "$G/UDC" 2>/dev/null
    killall adbd 2>/dev/null
    sleep 1
    umount "$FFS" 2>/dev/null
    for l in "$C/acm.GS0" "$C/ffs.adb" "$C/ncm.usb0" "$C/rndis.usb0" "$G/os_desc/FunKey.1"; do
        [ -L "$l" ] && rm "$l"
    done
    rmdir "$C/strings/0x409" "$C" "$G/functions/acm.GS0" "$G/functions/ffs.adb" \
          "$G/functions/ncm.usb0" "$G/functions/rndis.usb0" "$G/strings/0x409" "$G" 2>/dev/null
}

# build KIND: the gadget as /usr/local/sbin/adb builds it, with KIND (acm, ncm
# or rndis) next to ADB; returns 1 if it won't bind
build() {
    modprobe libcomposite
    modprobe usb_f_fs
    mkdir "$G" "$G/strings/0x409" "$C" "$C/strings/0x409"
    case "$1" in
        ncm|rndis)
            FUNC="$1.usb0"
            modprobe "usb_f_$1"
            mkdir "$G/functions/$FUNC"
            echo "$MAC_DEV" > "$G/functions/$FUNC/dev_addr"
            echo "$MAC_HOST" > "$G/functions/$FUNC/host_addr"
            NET="$(echo "$1" | tr 'a-z' 'A-Z')"
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
    if [ "$1" = rndis ]; then
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
    target="$UDC"
    [ -n "$USBLINK_FORCE_FAIL" ] && [ "$1" != acm ] && target="no-such-udc"
    if echo "$target" > "$G/UDC" 2>/dev/null; then return 0; fi
    log "bind of the $1 gadget failed: $(dmesg | tail -n 2 | tr '\n' ' ')"
    return 1
}

# rebuild: build KIND, or the stock gadget if KIND won't bind (then KIND=acm)
rebuild() {
    teardown
    build "$KIND" && return 0
    [ "$KIND" = acm ] && return 1
    log "falling back to the stock ADB + serial gadget"
    FELLBACK=1
    KIND=acm
    teardown
    build acm
}

# wait_host SECONDS: wait for the host to configure the gadget, logging what
# the Nano sees (USB power, its voltage, gadget state) whenever it changes:
# that is all there is to go on when a host does not connect
wait_host() {
    i=0
    last=""
    while [ $i -le "$1" ]; do
        v=0; read -r v < "$POWER/voltage_now" 2>/dev/null
        o=""; read -r o < "$POWER/online" 2>/dev/null
        st="$(state)"
        s="state $st, usb power $o at $((v / 1000000)).$((v / 100000 % 10)) V"
        [ "$s" != "$last" ] && log "  ${i}s: $s" && last="$s"
        [ "$st" = configured ] && return 0
        sleep 1
        i=$((i + 1))
    done
    return 1
}

# link_up: address, route and DNS on the network link and SSH, if this is a
# network gadget
link_up() {
    if [ -f "$DROPBEAR_PID" ]; then kill "$(cat "$DROPBEAR_PID")" 2>/dev/null; rm -f "$DROPBEAR_PID"; fi
    [ "$KIND" = acm ] && return
    IF="$(cat "$G/functions/$FUNC/ifname")"
    ROUTED=0; DHCP_FAILED=0
    net_config "$IF"
    [ "$SSHON" = 1 ] || return
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
    cp "$DIR/dropbear_ed25519_host_key" "$RUN/usblink-hostkey"
    chmod 600 "$RUN/usblink-hostkey"
    dropbear -s -r "$RUN/usblink-hostkey" -p "$IP:22" -P "$DROPBEAR_PID"
}

# no_host STATE: true for the gadget states that mean no host is talking to it.
# After a replug the stock gadget sits in "not attached" with USB power present.
# "suspended" is a host that put an idle device to sleep: leave that alone.
no_host() {
    case "$1" in
        "not attached"|powered|"") return 0 ;;
        *) return 1 ;;
    esac
}

# watch: the background loop of watch mode
# A rebuild disarms it; an unplug (USB power gone) or a host configuring the
# gadget (a working connection) arms it again. So a replug, or a host dropping a
# working connection while the port stays powered (a PC rebooting with the cable
# in, a host resetting its port), gets one rebuild, and a charger, which powers
# the Nano but never configures it, gets one per plug-in and no more.
watch() {
    echo $$ > "$WATCH_PID"
    log "watch: started"
    armed=0   # the run that started this one has just rebuilt
    prev_o=""
    prev_st=""
    while :; do
        o=0; read -r o < "$POWER/online" 2>/dev/null
        st=""; read -r st < "$UDCS/$UDC/state" 2>/dev/null
        if [ "$o" != 1 ]; then
            [ "$prev_o" != 0 ] && log "watch: unplugged"
            armed=1
        elif [ "$st" = configured ]; then
            armed=1
        elif no_host "$st" && [ "$armed" = 1 ]; then
            armed=0
            if [ "$prev_st" = configured ]; then
                why="host dropped the connection"
            else
                why="USB power but no host"
            fi
            # give a host the chance to configure it the normal way first
            n=0
            while [ $n -lt "$SETTLE" ] && no_host "$st"; do
                sleep "$TICK"
                read -r st < "$UDCS/$UDC/state" 2>/dev/null
                n=$((n + 1))
            done
            read -r o < "$POWER/online" 2>/dev/null
            if [ "$o" = 1 ] && no_host "$st"; then
                log "watch: $why (state $st), rebuilding"
                if [ -n "$USBLINK_DRYRUN" ]; then
                    log "watch: dry run, nothing rebuilt"
                else
                    rebuild
                    wait_host 15
                    link_up
                    st="$(state)"
                    log "watch: done, state $st"
                fi
            fi
        fi
        prev_o="$o"
        prev_st="$st"
        sleep "$TICK"
    done
}

if [ "$1" = --watch ]; then
    watch
    exit 0
fi

PLAN="gadget=$KIND address=$ADDRESS gateway=$GATEWAY dns=$(echo $DNS | tr ' ' ',') ssh=$SSHON watch=$WATCH mac_dev=$MAC_DEV mac_host=$MAC_HOST"
log "start: udc $UDC state $(state), usb power $(cat "$POWER/online" 2>/dev/null), $PLAN"

if [ -n "$USBLINK_DRYRUN" ]; then
    echo "plan: $PLAN"
    log "dry run, nothing changed"
    notice 3 "USB LINK: DRY RUN"
    exit 0
fi

# Stop a watcher from an earlier run first, so two never rebuild at once
if [ -f "$WATCH_PID" ]; then
    kill "$(cat "$WATCH_PID")" 2>/dev/null
    rm -f "$WATCH_PID"
fi

notice 0 "RECONNECTING USB..."
FELLBACK=0
rebuild
CONNECTED=0
wait_host 15 && CONNECTED=1
link_up
log "done: state $(state), connected $CONNECTED, fell back $FELLBACK, address $IP, routed $ROUTED"

if [ "$WATCH" = 1 ]; then
    # From a copy in RAM: the menu unmounts the OPK when this app exits
    cp "$SELF" "$WATCH_COPY"
    chmod +x "$WATCH_COPY"   # udhcpc runs it for DHCP events
    (cd / && setsid sh "$WATCH_COPY" --watch </dev/null >/dev/null 2>&1 &)
    AUTO="AUTO-RECONNECT ON"
else
    AUTO=""
fi

[ -z "$USBLINK_QUIET" ] && notif clear
if [ "$CONNECTED" = 1 ]; then
    if [ "$FELLBACK" = 1 ]; then
        notice 4 "USB LINK: CONNECTED" "ADB ONLY: NETWORK FAILED" ${AUTO:+"$AUTO"}
    elif [ "$KIND" = acm ]; then
        notice 3 "USB LINK: CONNECTED" "ADB" ${AUTO:+"$AUTO"}
    elif [ "$DHCP_FAILED" = 1 ]; then
        notice 4 "USB LINK: CONNECTED" "ADB + NETWORK ($NET)" "$IP" "NO DHCP: NO INTERNET" ${AUTO:+"$AUTO"}
    elif [ "$ROUTED" = 1 ]; then
        notice 3 "USB LINK: CONNECTED" "ADB + NETWORK ($NET)" "$IP" "INTERNET VIA THE HOST" ${AUTO:+"$AUTO"}
    else
        notice 3 "USB LINK: CONNECTED" "ADB + NETWORK ($NET)" "$IP" ${AUTO:+"$AUTO"}
    fi
elif [ "$WATCH" = 1 ]; then
    notice 4 "USB LINK: NO HOST YET" "PLUG THE CABLE IN:" "IT CONNECTS BY ITSELF"
else
    notice 4 "USB LINK: NO HOST FOUND" "PLUG THE CABLE IN" "AND TRY AGAIN"
fi
[ -z "$USBLINK_QUIET" ] && sleep 3
exit 0
