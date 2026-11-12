#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Share the host's internet connection with the RG Nano over USB.
# - On Windows / WSL: delegates to share-internet.ps1 (configures Windows ICS).
# - On Linux: configures 10.170.0.1/24 + ip_forward + NAT (+ dnsmasq if present).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE=""
SERIAL=""
ACTION="enable"
FORCE=0
YES=0
DRYRUN=0
CONFIG_NANO=0

usage() {
  cat <<'EOF'
Usage: usblink/share-internet.sh [options]

Share this PC's internet connection with the RG Nano over the USB network link.

Options:
  --source <iface>    Internet-facing adapter/interface (default: auto-detect)
  --serial <serial>   ADB serial when several devices are attached
  --configure-nano    On Windows/WSL, configure usblink.conf and start RNDIS via adb
  --status            Show detected Nano and internet adapters without changes
  --disable           Turn off internet sharing
  --force             Re-apply sharing even if already enabled
  -y, --yes           Skip confirmation prompts
  -n, --dry-run       Show what would be done without changing anything
  -h, --help          Show this help
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --source) SOURCE="${2:?missing value for --source}"; shift 2 ;;
    --serial) SERIAL="${2:?missing value for --serial}"; shift 2 ;;
    --configure-nano) CONFIG_NANO=1; shift ;;
    --status) ACTION="status"; shift ;;
    --disable) ACTION="disable"; shift ;;
    --force) FORCE=1; shift ;;
    -y|--yes) YES=1; shift ;;
    -n|--dry-run) DRYRUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

# WSL -> hand off to Windows share-internet.ps1
if [ -e /proc/sys/fs/binfmt_misc/WSLInterop ] || grep -qi microsoft /proc/version 2>/dev/null; then
  ps_exe="$(command -v powershell.exe 2>/dev/null || echo /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe)"
  [ -x "$ps_exe" ] || { echo "powershell.exe not found" >&2; exit 1; }
  ps1_win="$(wslpath -w "$HERE/share-internet.ps1")"
  args=(-NoProfile -ExecutionPolicy Bypass -File "$ps1_win")
  [ -n "$SOURCE" ] && args+=(-Source "$SOURCE")
  [ -n "$SERIAL" ] && args+=(-Serial "$SERIAL")
  [ "$ACTION" = "status" ] && args+=(-Status)
  [ "$ACTION" = "disable" ] && args+=(-Disable)
  [ "$FORCE" = 1 ] && args+=(-Force)
  [ "$YES" = 1 ] && args+=(-Yes)
  [ "$DRYRUN" = 1 ] && args+=(-DryRun)
  [ "$CONFIG_NANO" = 1 ] && args+=(-ConfigureNano)
  exec "$ps_exe" "${args[@]}"
fi

# Native Linux
find_nano_iface() {
  local net dev cur vid pid
  for net in /sys/class/net/*; do
    [ -e "$net/device" ] || continue
    dev="$(readlink -f "$net/device")"
    cur="$dev"
    while [ "$cur" != "/" ]; do
      if [ -f "$cur/idVendor" ] && [ -f "$cur/idProduct" ]; then
        vid="$(cat "$cur/idVendor" 2>/dev/null || true)"
        pid="$(cat "$cur/idProduct" 2>/dev/null || true)"
        if [ "$vid" = "8087" ] && { [ "$pid" = "011e" ] || [ "$pid" = "011f" ]; }; then
          basename "$net"
          return 0
        fi
        break
      fi
      cur="$(dirname "$cur")"
    done
  done
  return 1
}

find_internet_iface() {
  if [ -n "$SOURCE" ]; then
    printf '%s\n' "$SOURCE"
    return 0
  fi
  ip -4 route show default 2>/dev/null | awk '{ for (i=1; i<=NF; i++) if ($i=="dev") { print $(i+1); exit } }'
}

NANO_IF="$(find_nano_iface || true)"
INET_IF="$(find_internet_iface || true)"
HOST_CIDR="10.170.0.1/24"
SUBNET="10.170.0.0/24"
DNSMASQ_PID="/tmp/rg-nano-dnsmasq.pid"

if [ "$ACTION" = "status" ]; then
  echo "=== RG Nano USB Internet Status (Linux) ==="
  echo "Nano interface:    ${NANO_IF:-not attached}"
  if [ -n "$NANO_IF" ]; then
    ip -4 addr show "$NANO_IF" 2>/dev/null | sed 's/^/  /'
  fi
  echo "Internet source:   ${INET_IF:-none}"
  echo "ip_forward:        $(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null || echo unknown)"
  exit 0
fi

SUDO=""
if [ "$(id -u)" != 0 ]; then
  SUDO="sudo"
fi

run_root() {
  if [ "$DRYRUN" = 1 ]; then
    echo "[DryRun] $SUDO $*"
  else
    $SUDO "$@"
  fi
}

if [ "$ACTION" = "disable" ]; then
  if [ -f "$DNSMASQ_PID" ]; then
    run_root kill "$(cat "$DNSMASQ_PID")" 2>/dev/null || true
    run_root rm -f "$DNSMASQ_PID"
  fi
  if [ -n "$INET_IF" ] && command -v iptables >/dev/null 2>&1; then
    run_root iptables -t nat -D POSTROUTING -s "$SUBNET" -o "$INET_IF" -j MASQUERADE 2>/dev/null || true
  fi
  if [ -n "$NANO_IF" ]; then
    run_root ip addr del "$HOST_CIDR" dev "$NANO_IF" 2>/dev/null || true
  fi
  echo "USB internet sharing disabled."
  exit 0
fi

[ -n "$NANO_IF" ] || { echo "Nano USB network interface (8087:011e/011f) not found. On the Nano, open Connect -> USB -> Network: Linux, then Reconnect." >&2; exit 1; }
[ -n "$INET_IF" ] || { echo "No active default-route internet interface found (pass --source <iface>)." >&2; exit 1; }

echo "Sharing internet: $INET_IF -> $NANO_IF ($HOST_CIDR)"
if [ "$YES" != 1 ] && [ "$DRYRUN" != 1 ]; then
  read -r -p "Continue? [Y/n] " ans
  case "$ans" in ""|[Yy]*) ;; *) echo "Cancelled."; exit 0 ;; esac
fi

run_root ip addr replace "$HOST_CIDR" dev "$NANO_IF"
run_root ip link set "$NANO_IF" up
run_root sysctl -w net.ipv4.ip_forward=1 >/dev/null

if command -v iptables >/dev/null 2>&1; then
  if [ "$DRYRUN" = 1 ]; then
    echo "[DryRun] $SUDO iptables -t nat -A POSTROUTING -s $SUBNET -o $INET_IF -j MASQUERADE"
  elif ! $SUDO iptables -t nat -C POSTROUTING -s "$SUBNET" -o "$INET_IF" -j MASQUERADE 2>/dev/null; then
    $SUDO iptables -t nat -A POSTROUTING -s "$SUBNET" -o "$INET_IF" -j MASQUERADE
  fi
elif command -v nft >/dev/null 2>&1; then
  run_root nft add table ip rg_nano_nat 2>/dev/null || true
  run_root nft "add chain ip rg_nano_nat postrouting { type nat hook postrouting priority srcnat ; }" 2>/dev/null || true
  run_root nft add rule ip rg_nano_nat postrouting ip saddr "$SUBNET" oifname "$INET_IF" masquerade
else
  echo "Error: neither iptables nor nft is installed for NAT." >&2
  exit 1
fi

if command -v dnsmasq >/dev/null 2>&1; then
  [ -f "$DNSMASQ_PID" ] && run_root kill "$(cat "$DNSMASQ_PID")" 2>/dev/null || true
  run_root dnsmasq --interface="$NANO_IF" --bind-interfaces \
    --dhcp-range=10.170.0.50,10.170.0.100,1h --dhcp-option=option:router,10.170.0.1 \
    --dhcp-option=option:dns-server,1.1.1.1 --pid-file="$DNSMASQ_PID"
  echo "DHCP server running on $NANO_IF (Connect -> USB -> Network: Linux, Internet: via host)."
else
  echo "Done! Note: dnsmasq is not installed, so set GATEWAY=10.170.0.1 and DNS=1.1.1.1 in USB-link/usblink.conf (or install dnsmasq for DHCP)."
fi
