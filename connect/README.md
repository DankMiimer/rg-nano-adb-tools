# Connect

**One app instead of four.** Connect puts [Wi-Fi](../wifi/README.md),
[USB Link](../usblink/README.md) and the [USB Mode](../usbmode/README.md) switch
behind a single entry in the **Applications** menu (for **RG Nano**, **FunKey S**,
and **GBA Mini** running DrUm78's FunKey-OS builds), and adds the USB Link
settings that used to need a file edited on the PC. On the GBA Mini it draws
inside its 240×160 visible screen area and hides the Wi-Fi row on
peripheral-only devices (verified on GBA Mini; untested on hardware for FunKey S).

Nothing new runs underneath: Connect drives the same three scripts the separate
apps do (`wifi.sh`, `usblink.sh`, `usbmode.sh`), and their own pages describe
what those do, the settings files and the limits. Like them it works on the
stock firmware and changes nothing on the system partition. The Wi-Fi part only
loads into the kernel it was checked against (see [Safety](../wifi/README.md#safety)).

> **Status:** built, tested offline (44 tests), and verified on RG Nano and GBA Mini. FunKey S support follows the same `funkey-s` / `240×240` FunKey-OS layout and is tested offline.

## The screens

```
Connect
  Wi-Fi          off / on / the network's name    -> the Wi-Fi Setup screens
  USB            ADB / ADB + net / storage / ...  -> the USB page
  Handheld Link                                   -> file sharing with another handheld
  About
  Exit
```

**Handheld Link** is the app from the
[handheld-usb-link](https://github.com/DankMiimer/handheld-usb-link) project,
bundled as it is when its OPK is at hand at build time (see Install). Choosing
it hands the screen over to it; leaving Handheld Link goes back to the Nano's
menu, not to Connect, because the Nano's 64 MB has no room for both at once. It
waits for Wi-Fi to be off: the dongle and the other handheld need the same port.

The USB page, in ADB mode:

| Row | What A does |
|---|---|
| **Reconnect** | rebuilds the USB connection, as opening USB Link does, and shows the result (connected or not, the network link's address, internet or not) |
| **Network** | steps through off, Windows (`NETWORK=rndis`) and Linux (`NETWORK=ncm`) |
| **Internet** | with a network link: use the host's internet connection or not (`ADDRESS=dhcp`; on a Windows link it also sets `DNS=1.1.1.1`, see [Internet over the cable](../usblink/README.md#internet-over-the-cable)). On Windows, run [`../usblink/Share-Internet.cmd`](../usblink/Share-Internet.cmd) (or `../usblink/share-internet.sh` on Linux/WSL) to share the host's connection automatically |
| **Auto** | auto-reconnect after replugs, on or off (`WATCH`) |
| **Mode** | asks, then reboots into USB storage mode |
| **Status** | mode, host, link address, whether the auto-reconnect watcher runs |

A changed setting is written to `USB-link/usblink.conf` at once, and the top
row turns into **Apply**: nothing reconnects until you press it. A `GATEWAY`
written by hand shows as "custom" and is left alone.

In storage mode the page offers **Mode** (reboot into ADB) and **Status**. While
the Wi-Fi dongle has the port, it says so: the Nano has one USB port.

Opened with a cable in and no host talking to it (the moment USB Link is for),
the cursor starts on **USB**, so it is A, A to reconnect. Start or Menu leaves
the app from any screen.

## Install

**From source** (the Nano must be in ADB mode):

```bash
connect/build_connect_opk.sh --refresh
```

The binaries (kernel modules, `wpa_supplicant`, the Python runtime) are the ones
[`wifi/build_wifi_opk.sh`](../wifi/build_wifi_opk.sh) builds; this script takes
them from that build's work directory and runs it first if they are missing. See
[Building](../wifi/README.md#building) for what that needs. Other forms:

```bash
connect/build_connect_opk.sh --build-only   # just build Connect.opk
connect/build_connect_opk.sh                # install, but don't restart the menu
connect/build_connect_opk.sh --retire-old   # park the four entries it replaces
```

Handheld Link is included when the build finds `HandheldLink.opk`: in a
`handheld-usb-link` checkout next to this repository
(`handheld-link/funkey/HandheldLink.opk`), or wherever `HLINK_OPK` points. The
build says which it did. Without it, Connect has no Handheld Link row.

`--retire-old` renames `WiFi.opk`, `USBLink.opk`, `SwitchToUSB.opk` and
`SwitchToADB.opk` in the Nano's `Applications` folder to `*.opk.off`, which the
menu ignores. Nothing is deleted: rename one back to get it back. Settings,
saved networks and logs are where they were (`USB-link/`, `FunKey/wifi/`,
`FunKey/usblink.log`), so the old apps and Connect can be mixed freely.

The separate OPKs still build from the same sources, for a device where only one
of them makes sense.

## Files

- [`app/connect_gui.py`](app/connect_gui.py): the hub and the USB page. The
  Wi-Fi page and the drawing code are `wifi/app/wifi_gui.py`.
- [`app/usb_backend.py`](app/usb_backend.py): reads the USB state, edits
  `usblink.conf`, runs `usblink.sh` and `usbmode.sh`.
- [`run.sh`](run.sh): the launcher. Errors go to `FunKey/connect.log`.
- [`../tests/test_connect.py`](../tests/test_connect.py): offline tests with a
  stand-in USB port and scripted button presses. `CONNECT_SHOTS=<dir>` saves the
  screens they draw as PNGs.
