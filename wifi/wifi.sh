#!/bin/sh
# Wi-Fi for the stock RG Nano firmware, with a USB Wi-Fi dongle (RTL8188FU /
# RTL8188FTV, USB ID 0bda:f179) on the USB-C port through an OTG adapter.
#
# The stock kernel has no Wi-Fi stack, so this brings its own: cfg80211 and the
# rtl8188fu driver as modules built for the factory kernel (see kernel/), plus
# wpa_supplicant. Nothing on the system partition changes; a reboot undoes it.
#
#   wifi.sh on       USB port to host mode, load the driver, start
#                    wpa_supplicant (joins saved networks) and DHCP
#   wifi.sh off      stop all of that and give the port back to the cable
#   wifi.sh scan     list networks: signal<TAB>flags<TAB>ssid (wpa_cli format)
#   wifi.sh status   key=value lines: wifi=on|off, wpa_state, ssid, ip, rssi
#   wifi.sh cli ...  wpa_cli on the running wpa_supplicant
#
# Exit status of `on`: 0 on, 3 kernel not supported, 4 a USB cable is plugged
# in, 5 no dongle found, 6 wpa_supplicant failed.
#
# The daemons, modules and firmware run from a copy in RAM ($RUN), because the
# menu unmounts the OPK when the app exits and Wi-Fi has to outlive it.
#
# Settings, all optional, in $DATA/wifi.conf (KEY=value lines):
#   SSH=yes|no     yes: SSH server (dropbear, key logins only) on Wi-Fi, with
#                  the keys in /mnt/USB-link/authorized_keys (default no)
#   TZ=<POSIX TZ>  time zone for Sync Clock, e.g. CET-1CEST,M3.5.0,M10.5.0/3;
#                  without it the clock is left alone (the firmware keeps the
#                  clock in local time, so it can't be set without knowing it)
#
# Test hooks: WIFI_RUN (default /tmp/wifi), WIFI_DATA (default /mnt/FunKey/wifi),
# WIFI_MUSB (the musb sysfs dir), WIFI_POWER (the USB power supply dir).

HERE="$(cd "$(dirname "$0")" && pwd)"
RUN="${WIFI_RUN:-/tmp/wifi}"
DATA="${WIFI_DATA:-/mnt/FunKey/wifi}"
MUSB="${WIFI_MUSB:-/sys/devices/platform/soc/1c19000.usb/musb-hdrc.1.auto}"
POWER="${WIFI_POWER:-/sys/class/power_supply/axp20x-usb}"
LOG="$DATA/wifi.log"
CONF="$DATA/wpa_supplicant.conf"
CTRL="$RUN/ctrl"
IF=wlan0

log() { mkdir -p "$DATA"; echo "$(date '+%F %T') $*" >> "$LOG" 2>/dev/null; }
setting() { sed -n "s/^$1=//p" "$DATA/wifi.conf" 2>/dev/null | tail -n 1 | tr -d '\r"'; }
cli() { "$RUN/wpa_cli" -p "$CTRL" -i "$IF" "$@"; }
alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

# known_kernel: the modules are only loaded into kernels someone has audited
# them against (uname -r + uname -v in kernels). Vermagic alone can't tell two
# differently configured 4.14.14-funkey builds apart, and a mismatch corrupts
# memory instead of failing cleanly.
known_kernel() {
    id="$(uname -r) $(uname -v)"
    while IFS= read -r line; do
        [ "$line" = "$id" ] && return 0
    done < "$RUN/kernels"
    log "kernel not in the supported list: $id"
    return 1
}

# stage: copy what has to outlive the OPK into RAM
stage() {
    [ "$HERE" = "$RUN" ] && return
    mkdir -p "$RUN/fw/rtlwifi"
    cp "$HERE/wifi.sh" "$HERE/kernels" "$HERE/bin/wpa_supplicant" "$HERE/bin/wpa_cli" \
       "$HERE/modules/cfg80211.ko" "$HERE/modules/rtl8188fu.ko" "$RUN/"
    cp "$HERE/firmware/rtl8188fufw.bin" "$RUN/fw/rtlwifi/"
    chmod +x "$RUN/wifi.sh" "$RUN/wpa_supplicant" "$RUN/wpa_cli"
}

is_host() { case "$(cat "$MUSB/mode" 2>/dev/null)" in a_*) return 0 ;; *) return 1 ;; esac; }

# A fixed, locally administered MAC from the CPU serial, so the router sees the
# same device every time (the driver makes up a random one otherwise)
mac() {
    s="$(sed -n 's/^Serial[[:space:]]*: *//p' /proc/cpuinfo)"
    echo "0e$(printf '%s' "$s" | tail -c 10 | sed 's/\(..\)/:\1/g')"
}

start_ssh() {
    [ "$(setting SSH)" = yes ] && [ -s /mnt/USB-link/authorized_keys ] || return 0
    alive "$RUN/dropbear.pid" && return 0
    # /root is read-only and dropbear only reads ~/.ssh/authorized_keys, so
    # /root becomes a small RAM copy of itself with the key added (as USB Link)
    if ! touch /root/.wifi 2>/dev/null; then
        rm -rf /tmp/wifi-root; mkdir -p /tmp/wifi-root
        cp -a /root/. /tmp/wifi-root/
        mount -t tmpfs -o size=256k,mode=0755 tmpfs /root
        cp -a /tmp/wifi-root/. /root/
        rm -rf /tmp/wifi-root
    fi
    rm -f /root/.wifi
    mkdir -p /root/.ssh
    tr -d '\r' < /mnt/USB-link/authorized_keys > /root/.ssh/authorized_keys
    chmod 700 /root/.ssh; chmod 600 /root/.ssh/authorized_keys
    # USB Link's server holds port 22 on the USB address, which is gone in
    # host mode, and would keep this one off IPv4
    [ -f /tmp/usblink-dropbear.pid ] && kill "$(cat /tmp/usblink-dropbear.pid)" 2>/dev/null && rm -f /tmp/usblink-dropbear.pid
    key=/mnt/USB-link/dropbear_ed25519_host_key
    [ -s "$key" ] || key="$DATA/dropbear_ed25519_host_key"
    [ -s "$key" ] || dropbearkey -t ed25519 -f "$key" >/dev/null 2>&1
    cp "$key" "$RUN/hostkey"; chmod 600 "$RUN/hostkey"
    dropbear -s -r "$RUN/hostkey" -p 22 -P "$RUN/dropbear.pid"
    log "ssh: on"
}

wifi_on() {
    mkdir -p "$RUN" "$DATA"
    if [ -d "/sys/class/net/$IF" ] && alive "$RUN/wpa.pid"; then
        start_ssh
        return 0
    fi
    stage
    known_kernel || return 3
    if ! is_host; then
        # USB power with the port in device mode is a cable to a PC or a
        # charger: switching to host mode would put the Nano's 5 V against it
        o=0; read -r o < "$POWER/online" 2>/dev/null
        if [ "$o" = 1 ]; then
            log "on: a USB cable is plugged in, not switching the port"
            return 4
        fi
        # USB Link's watcher would rebuild the USB gadget under the host port
        [ -f /tmp/usblink-watch.pid ] && kill "$(cat /tmp/usblink-watch.pid)" 2>/dev/null && rm -f /tmp/usblink-watch.pid
        echo host > "$MUSB/mode"
        log "on: port in host mode"
    fi
    printf '%s' "$RUN/fw" > /sys/module/firmware_class/parameters/path
    [ -d /sys/module/cfg80211 ] || insmod "$RUN/cfg80211.ko" || { log "on: cfg80211 did not load"; return 3; }
    [ -d /sys/module/rtl8188fu ] || insmod "$RUN/rtl8188fu.ko" ifname="$IF" \
        || { log "on: rtl8188fu did not load"; return 3; }
    i=0
    while [ ! -d "/sys/class/net/$IF" ] && [ $i -lt 20 ]; do sleep 0.5; i=$((i + 1)); done
    if [ ! -d "/sys/class/net/$IF" ]; then
        log "on: no dongle found ($(cat /sys/bus/usb/devices/*/idVendor 2>/dev/null | tr '\n' ' '))"
        wifi_off
        return 5
    fi
    # The driver's own rtw_initmac refuses locally administered addresses and
    # then makes up a random one; setting it on the interface has no such check
    ip link set "$IF" address "$(mac)" || log "on: could not set the MAC address"
    ip link set "$IF" up
    if [ ! -s "$CONF" ]; then
        printf 'ctrl_interface=%s\nupdate_config=1\n' "$CTRL" > "$CONF"
    fi
    "$RUN/wpa_supplicant" -B -i "$IF" -D nl80211 -c "$CONF" -C "$CTRL" -P "$RUN/wpa.pid" >> "$LOG" 2>&1
    i=0
    while ! alive "$RUN/wpa.pid" && [ $i -lt 10 ]; do sleep 0.5; i=$((i + 1)); done
    alive "$RUN/wpa.pid" || { log "on: wpa_supplicant did not start"; return 6; }
    # DHCP in the background: it keeps trying until a network is joined, renews
    # leases, and the firmware's script sets the route and /etc/resolv.conf
    alive "$RUN/udhcpc.pid" || udhcpc -i "$IF" -b -p "$RUN/udhcpc.pid" -t 5 -T 3 -A 5 -x hostname:rg-nano >/dev/null 2>&1
    start_ssh
    # Unplugging the dongle turns Wi-Fi off and gives the port back to device
    # mode at once: a cable plugged in while the port still supplies 5 V would
    # put the Nano's 5 V against the PC's
    alive "$RUN/watch.pid" || (cd / && setsid sh "$RUN/wifi.sh" watch </dev/null >/dev/null 2>&1 &)
    log "on: wifi on, mac $(cat /sys/class/net/$IF/address)"
    return 0
}

# watch: until Wi-Fi is turned off, turn it off when the dongle goes away
wifi_watch() {
    echo $$ > "$RUN/watch.pid"
    while alive "$RUN/wpa.pid"; do
        if [ ! -d "/sys/class/net/$IF" ]; then
            log "watch: the dongle was unplugged"
            rm -f "$RUN/watch.pid"
            wifi_off
            return
        fi
        sleep 1
    done
    rm -f "$RUN/watch.pid"
}

wifi_off() {
    for p in watch dropbear udhcpc wpa; do
        [ -f "$RUN/$p.pid" ] && kill "$(cat "$RUN/$p.pid")" 2>/dev/null
        rm -f "$RUN/$p.pid"
    done
    sleep 1
    ip link set "$IF" down 2>/dev/null
    # forget this interface's DNS servers (the firmware's DHCP script tags them)
    if [ -f /etc/resolv.conf ]; then
        t="$(grep -v "# $IF\$" /etc/resolv.conf)"
        printf '%s\n' "$t" > /etc/resolv.conf
    fi
    rmmod rtl8188fu 2>/dev/null
    rmmod cfg80211 2>/dev/null
    if is_host; then
        echo peripheral > "$MUSB/mode"
        log "off: port back in device mode"
    fi
    log "off: wifi off"
}

wifi_status() {
    if [ -d "/sys/class/net/$IF" ] && alive "$RUN/wpa.pid"; then
        echo wifi=on
        cli status 2>/dev/null | grep -E '^(wpa_state|ssid|ip_address|freq)='
        cli signal_poll 2>/dev/null | grep -E '^RSSI='
        echo "ip=$(ip -4 addr show "$IF" 2>/dev/null | sed -n 's/.*inet \([0-9.]*\).*/\1/p' | head -n 1)"
    else
        echo wifi=off
    fi
}

wifi_scan() {
    alive "$RUN/wpa.pid" || return 1
    cli scan >/dev/null
    # results come in over a few seconds; stop once two reads agree
    prev=""; i=0
    while [ $i -lt 10 ]; do
        sleep 1
        cur="$(cli scan_results 2>/dev/null | tail -n +2)"
        [ -n "$cur" ] && [ "$cur" = "$prev" ] && [ $i -ge 3 ] && break
        prev="$cur"; i=$((i + 1))
    done
    cli scan_results 2>/dev/null | tail -n +2 | cut -f 3-
}

case "$1" in
    on) wifi_on ;;
    off) wifi_off ;;
    status) wifi_status ;;
    scan) wifi_scan ;;
    cli) shift; cli "$@" ;;
    watch) wifi_watch ;;
    *) echo "usage: wifi.sh on|off|scan|status|cli ..." >&2; exit 2 ;;
esac
