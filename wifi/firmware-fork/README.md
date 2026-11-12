# Wi-Fi built into the firmware (notes for a DrUm78 FunKey-OS fork)

The [Wi-Fi OPK](../README.md) one folder up adds Wi-Fi to the **stock** image
without touching it. Before that, in June 2026, the same dongle ran on a
**custom build** of DrUm78's FunKey-OS with Wi-Fi in the firmware itself. If you
are forking the OS, that is the route you want, and this folder is what is left
of it.

**What this is and is not.** The Buildroot tree of that build no longer exists.
The three scripts under `rootfs-overlay/` were read back out of the built
firmware image (`FunKey-rootfs-DrUm78_RG_Nano.fwu`, 2026-06-15), so they are
exactly what ran on the device. The kernel and Buildroot settings below come
from the notes written at the time and from what the image contains; the
package files themselves (`rtl8188fu.mk` and the edited `linux.config` /
`funkey_defconfig`) are lost. Nothing here has been rebuilt since June.

Dongle: **RTL8188FU / RTL8188FTV**, USB ID `0bda:f179`, on a USB-C OTG adapter.

## What the build changed

### 1. Kernel (`linux.config`)

```
CONFIG_WIRELESS=y
CONFIG_CFG80211=m
CONFIG_CFG80211_WEXT=y
CONFIG_MAC80211=m
CONFIG_WLAN=y
```

- The driver only needs `cfg80211` (the image's `modules.dep` has
  `extra/rtl8188fu.ko: kernel/net/wireless/cfg80211.ko`). `mac80211` was built
  but nothing used it; leave it out unless you add a mac80211 driver.
- USB host support is already in the factory configuration
  (`CONFIG_USB_MUSB_DUAL_ROLE=y`, `CONFIG_USB_MUSB_SUNXI=y`); nothing to add.
- The kernel source is **DrUm78/linux**, commit
  `1f6d3fc2f17bc1d31aa2cf89d5642da8bbf4517b`, not upstream 4.14.14. The complete
  factory configuration with `CFG80211=m` added is in
  [`../kernel/config`](../kernel/config).

### 2. The driver, as a Buildroot kernel-module package

Source: [kelebek333/rtl8188fu](https://github.com/kelebek333/rtl8188fu) at
commit `c8c95708b3756c67139c456a2a6576c1e6491d82` (the hash that was in the
package at first did not exist and gave a 404). The driver must be built for
cfg80211: `-DCONFIG_IOCTL_CFG80211 -DRTW_USE_CFG80211_STA_EVENT`. The upstream
Makefile at that commit already sets both in its default platform block.

The image has the module at `/lib/modules/4.14.14-funkey/extra/rtl8188fu.ko` and
the firmware at `/lib/firmware/rtlwifi/rtl8188fufw.bin`.

The package file is lost. An **untested** reconstruction, to save typing:

```make
RTL8188FU_VERSION = c8c95708b3756c67139c456a2a6576c1e6491d82
RTL8188FU_SITE = $(call github,kelebek333,rtl8188fu,$(RTL8188FU_VERSION))
RTL8188FU_LICENSE = GPL-2.0
RTL8188FU_MODULE_MAKE_OPTS = CONFIG_RTL8188FU=m \
	USER_EXTRA_CFLAGS="-DCONFIG_LITTLE_ENDIAN -DCONFIG_IOCTL_CFG80211 -DRTW_USE_CFG80211_STA_EVENT"

define RTL8188FU_INSTALL_FIRMWARE
	$(INSTALL) -D -m 0644 $(@D)/firmware/rtl8188fufw.bin \
		$(TARGET_DIR)/lib/firmware/rtlwifi/rtl8188fufw.bin
endef
RTL8188FU_POST_INSTALL_TARGET_HOOKS += RTL8188FU_INSTALL_FIRMWARE

$(eval $(kernel-module))
$(eval $(generic-package))
```

A module build command that is tested (outside Buildroot, against the same
kernel tree) is in [`../build_wifi_opk.sh`](../build_wifi_opk.sh); search for
`CONFIG_RTL8188FU=m`.

### 3. Buildroot packages (`funkey_defconfig`)

```
BR2_PACKAGE_WPA_SUPPLICANT=y
BR2_PACKAGE_WPA_SUPPLICANT_PASSPHRASE=y
BR2_PACKAGE_WIRELESS_TOOLS=y
BR2_PACKAGE_WIRELESS_TOOLS_LIB=y
```

`wpa_passphrase`, `iwconfig` and `iwlist` are used by the `wifi` script below.
Dropbear and `udhcpc` were already in the firmware.

### 4. Root filesystem overlay

Drop these into `FunKey/board/funkey/rootfs-overlay/`:

| File | What it does |
|---|---|
| `etc/init.d/S40wifi` | At boot: USB port to host mode, load the driver, set the MAC address, start `wpa_supplicant` with `/mnt/wpa_supplicant.conf`, then DHCP or a static address |
| `usr/local/sbin/wifi` | `wifi scan`, `wifi connect <ssid> [password]`, `wifi status`, `wifi disconnect`, or a text menu with no arguments |
| `etc/init.d/S42dropbear` | The stock script plus a bind mount of `/mnt/FunKey/.ssh` on `/root/.ssh`, so `authorized_keys` on the SD card works |

For the bind mount, `/root/.ssh` has to be a real, empty directory in the
overlay. In the stock overlay it is a symlink into the vfat partition, and
Dropbear refuses keys there because of the permissions.

Files on the SD card's data partition (`/mnt`) that the scripts read or write:

- `wpa_supplicant.conf`: the network, written by `wifi connect`;
- `ip.txt` (optional): `IP=`, `NETMASK=`, `GATEWAY=`, `DNS=` for a static
  address; without it, DHCP;
- `usbnet`: the stock marker file; Dropbear only starts when it exists;
- `usb_debug.log`, `wifi_debug.log`: logs of the last start.

## Things to know before you copy it

- **`S40wifi` switches the port to host mode on every boot, cable or not.** In
  host mode the Nano puts 5 V on the port, so booting with a PC or charger
  plugged in sets the Nano's 5 V against theirs, and with the port in host mode
  there is no ADB or USB drive either. The OPK's [`wifi.sh`](../wifi.sh) fixes both: it
  refuses to switch while USB power is present, and a watcher switches back
  when the dongle is unplugged. Take that logic.
- **The driver invents a random MAC address on every start** (its `rtw_initmac`
  also rejects locally administered addresses passed as a parameter). Both
  versions set one with `ip link set wlan0 address` before `up`, made from the
  CPU serial, so the router sees one device.
- **Wireless extensions are fine here and nowhere else.** `CFG80211_WEXT` adds
  fields to `struct net_device` and `struct net`. When you rebuild the whole
  kernel that is harmless. A module built with it and loaded into the factory
  kernel corrupts memory, which is why the OPK keeps it off and uses nl80211.
- Module parameters used: `rtw_power_mgnt=0 rtw_enusbss=0 rtw_ips_mode=0` (no
  power saving, no USB selective suspend).
- Building FunKey-OS from a Windows drive under WSL: CRLF line endings broke
  Buildroot (`LINKS_OPK_SITE cannot be empty`), and a login shell's PATH made it
  stop with "Your PATH contains a newline". Keep the tree on LF and build with a
  clean PATH, or simply keep it on the Linux filesystem.
- One USB port: the dongle or the cable, never both, and no charging while the
  dongle is in.

## What to take from the OPK instead

The June scripts are the first working version. The OPK code is the later one,
and better in every part that is not about packaging:

- [`../wifi.sh`](../wifi.sh): on / off / scan / status with nl80211, the power
  check, the unplug watcher, saved networks without plain-text passwords, SSH;
- [`../app/`](../app): the settings screen (network list, on-screen keyboard),
  pure Python drawing on `/dev/fb0` and reading `/dev/input/event0`, no SDL.
  `nano_fb.py` is a small toolkit you can reuse for other apps;
- [`../build_wifi_opk.sh`](../build_wifi_opk.sh): cross-builds of
  `wpa_supplicant` 2.11 and a Python 3.12 runtime with TLS for this device
  (64 MB RAM, musl, armhf), from pinned sources.

In a fork none of the OPK's safety machinery (export audit, kernel whitelist,
`-fno-common`) is needed: it exists only because those modules go into a kernel
somebody else built.
