# Wi-Fi

An OPK that gives the RG Nano **Wi-Fi with a USB dongle on the stock
firmware**: no custom build, nothing changed on the system partition. It adds a
**Wi-Fi** entry to the Applications menu with a settings screen (scan, pick a
network, type the password on an on-screen keyboard), and Wi-Fi keeps running
in the background after you leave it, for apps that use the network.

Supported dongle: **RTL8188FU / RTL8188FTV** (USB ID `0bda:f179`), the common
"150 Mbps" nano dongle, on the Nano's USB-C port through a USB-C OTG adapter.

It only loads into the kernel it was checked against: **DrUm78's factory RG Nano
image** (`4.14.14-funkey #1 SMP Sun Jan 18 03:17:24 CET 2026`, the one that
reports `FunKey-OS 2.3.0` and has ADB). On any other kernel it says "not
supported" and does nothing. See [Safety](#safety).

## Install

**From source** (the Nano must be in ADB mode; see [Building](#building) for
what the build needs):

```bash
wifi/build_wifi_opk.sh --refresh
```

Or copy `WiFi.opk` into the `Applications` folder of the Nano's drive yourself.

## Use

1. Unplug the USB cable and plug the dongle in through the OTG adapter. The port
   is either the cable or the dongle, so the Nano runs on its battery meanwhile.
2. Open **Applications → Wi-Fi → Scan & Connect**, pick your network and type the
   password (A types, X space, Y backspace, B or OK when done).
3. It connects, sets the clock from the internet (once you have set the time
   zone, see [Settings](#settings)) and remembers the network. **Exit** leaves
   Wi-Fi on.

Next time, **Wi-Fi On** joins a saved network without scanning. **Wi-Fi Off**,
or simply unplugging the dongle, stops everything and gives the port back to the
cable; plug it into the PC and open USB Link (or reboot) to get ADB back. A
reboot always ends Wi-Fi.

While Wi-Fi is on, the port powers the dongle, so **unplug the dongle before
plugging a cable in**: Wi-Fi notices within a second or two and switches the
port back. A cable into a port that is still supplying 5 V puts the Nano's 5 V
against the PC's.

Networks are saved in `FunKey/wifi/wpa_supplicant.conf` on the drive, **without
the password**: only the key WPA derives from it (`psk=`), which is all that is
needed to join. Delete the file to forget them all.

### Settings

Optional, in `FunKey/wifi/wifi.conf` on the drive, one `KEY=value` per line:

| Setting | Values | Default |
|---|---|---|
| `SSH` | `yes`: an SSH server on Wi-Fi (key logins only), with the keys from `USB-link/authorized_keys` | `no` |
| `TZ` | the time zone for the clock, as a POSIX TZ string; Central Europe is `CET-1CEST,M3.5.0,M10.5.0/3` | none: the clock is not set |

The firmware keeps the clock in local time, so the clock can only be set from
the internet once the app knows the time zone. Without `TZ`, connecting leaves
the clock alone and Sync Clock says to set it.

### From a shell

`wifi.sh` does the work and can be used directly (as root, with the OPK mounted
or from its copy in `/tmp/wifi` once Wi-Fi has been on):

```
wifi.sh on       # port to host mode, driver, wpa_supplicant, DHCP
wifi.sh scan     # signal  flags  ssid
wifi.sh status
wifi.sh cli ...  # wpa_cli on the running wpa_supplicant
wifi.sh off
```

## What it does

The stock kernel has USB host support but no Wi-Fi stack, and the firmware has
no `wpa_supplicant`. The OPK brings:

- **`cfg80211.ko`** and **`rtl8188fu.ko`**, built for the factory kernel, and the
  dongle's firmware file;
- **`wpa_supplicant` / `wpa_cli` 2.11** (nl80211, using the firmware's own libnl);
- a **Python 3.12** runtime for the settings screen (the firmware has no Python).

`wifi.sh on`:

1. refuses if USB power is present with the port in device mode (a cable to a PC
   or a charger: switching would put the Nano's 5 V against it), and stops USB
   Link's auto-reconnect watcher;
2. switches the USB port to host mode (`musb-hdrc` `mode`), which powers the
   dongle;
3. points the kernel's firmware loader at its copy of the firmware, loads the two
   modules and gives the interface a fixed MAC address made from the CPU serial
   (left alone, the driver makes up a random one each time, and the router would
   see a new device on every start);
4. starts `wpa_supplicant` (joins saved networks) and the firmware's `udhcpc`
   (address, route and DNS, renewed in the background), and SSH if enabled;
5. starts a watcher that turns all of it off, and the port back to device mode,
   when the dongle is unplugged.

Everything that has to outlive the app runs from a copy in `/tmp/wifi`, since the
menu unmounts the OPK when the app exits. Logs: `FunKey/wifi/wifi.log` and
`gui.log`.

## Safety

Loading a kernel module built for a different kernel configuration doesn't fail
cleanly: two builds of `4.14.14-funkey` can share the module "vermagic" exactly
and still disagree about the layout of core structures, and the result is memory
corruption. So:

- the modules are built from **DrUm78's RG Nano kernel source** with
  [`kernel/config`](kernel/config), the factory configuration plus
  `CFG80211=m`. Wireless extensions (`CFG80211_WEXT`) stay off on purpose: they
  add fields to `struct net_device` and `struct net`, which the factory kernel
  doesn't have. The build checks that the configuration comes out unchanged;
- `wifi.sh` only loads them into a kernel listed in [`kernel/kernels`](kernel/kernels)
  (`uname -r` and `uname -v`); anything else gets "not supported";
- `build_wifi_opk.sh --audit` pulls the Nano's `/boot/zImage` and checks that
  every symbol the two modules import is exported by it
  ([`kernel/audit_exports.py`](kernel/audit_exports.py)). On the factory kernel
  all 128 (cfg80211) and 151 (rtl8188fu) resolve.

Without wireless extensions, old tools like `iwconfig` and `iwlist` don't work;
`wpa_supplicant` and `wpa_cli` use nl80211 instead.

## Limits

- **WPA2 (and open) networks only.** This `wpa_supplicant` is built with its own
  small crypto library, which has no WPA3 (SAE). WPA2/WPA3 "transition" networks
  work; WPA3-only, WEP and enterprise (login) networks are listed but refused.
- No charging while the dongle is in, and no ADB: the Nano has one USB port.
- One dongle chipset. Other chipsets need their own driver built the same way.

## Building

```bash
wifi/build_wifi_opk.sh --build-only
```

Needs the [FunKey SDK 2.3.0](https://github.com/FunKey-Project/FunKey-OS/releases)
toolchain (`FUNKEY_SDK`, default `~/FunKey-sdk-2.3.0`), `bash`, `curl`, `git`,
`make`, `gcc`, `bc`, `mksquashfs`, Python **3.12** on the build machine (it
compiles the runtime's bytecode, which must match), and `lzop` for `--audit`.
Tested on Ubuntu 24.04 under WSL2.

Everything is fetched from pinned sources: the kernel by git commit, the rest
checked against SHA-256 sums in the script. Work files (about 2 GB with the
kernel tree) go to `~/.cache/rg-nano-adb-tools/wifi` (`WIFI_WORK`); the Python
runtime ends up in `runtime/` there, for other OPKs that need Python.

## Licenses

The scripts and the settings app are MIT like the rest of this repository. The
OPK also contains: Linux kernel modules (GPL-2.0: DrUm78/linux and
kelebek333/rtl8188fu), Realtek's firmware for the RTL8188FU (as distributed with
that driver), wpa_supplicant (BSD), CPython (PSF License), OpenSSL 1.1.1 (OpenSSL
License), QuickJS (MIT), the Mozilla CA bundle (MPL-2.0), and a bitmap made from
the Terminus font (SIL Open Font License 1.1) in `app/nano_font.py`.
