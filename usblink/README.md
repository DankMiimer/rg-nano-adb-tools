# USB Link

One OPK for the Nano's **Applications** menu that brings its USB connection back
**without a reboot**, keeps doing so after every replug, and can add a **network
link** over the same cable.

The Nano only talks to a USB host that was plugged in when its USB was set up at
boot. Unplug the cable and plug it back in, and ADB is gone until you reboot (see
[../docs/FIRMWARE.md](../docs/FIRMWARE.md)). Open **USB Link** once and it comes
back in a few seconds; after that, every replug reconnects by itself until the
next reboot.

It works with the stock firmware: no custom build, nothing changed on the system
partition. It only works in **ADB mode** (see [../usbmode/README.md](../usbmode/README.md)).

## Install

**From source:** the Nano must be in ADB mode (the installer talks to it with
`nanoctl`).

```bash
usblink/build_usblink_opk.sh --refresh
```

Needs `mksquashfs` (squashfs-tools) and `python3` (for the icon). Other forms:

```bash
usblink/build_usblink_opk.sh --build-only   # just build USBLink.opk
usblink/build_usblink_opk.sh                # install, but don't restart the menu
```

Or copy `USBLink.opk` into the `Applications` folder of the Nano's drive yourself.

## Use

1. Plug the Nano in with a **USB-A to USB-C cable** (or a USB-C to USB-A adapter
   on the host). A USB-C to USB-C cable gives the Nano no power at all, so nothing
   happens: no host will switch on its 5 V for the Nano's port.
2. Open **Applications → USB Link**. It shows "RECONNECTING USB...", then
   "CONNECTED" (or "NO HOST FOUND") and returns to the menu after a few seconds.

ADB drops for a few seconds while it rebuilds. The notice also says
"AUTO-RECONNECT ON": from now until the Nano reboots, unplug and plug in again
(to the same host or another) and the connection comes back by itself, about
6-10 seconds after plugging in. No need to open USB Link again.

### Auto-reconnect (watch mode)

After a run, a small watcher stays in the background until the next reboot. When
the Nano has USB power but no host has connected after about 6 seconds, it
rebuilds the USB connection, the same way the app does.

- **Once per plug-in.** A charger powers the Nano but never connects to it: it
  gets one rebuild and no more, until you unplug.
- **It never touches a working connection** (a host that has configured the
  Nano, or put it to sleep), so it can't cut ADB while you use it.
- **It costs next to nothing:** it checks two status files once a second with the
  shell's own `read`, no extra programs.
- It runs from a copy in `/tmp`, since the menu unmounts the OPK when the app
  exits. Opening USB Link again replaces it; a reboot ends it. `WATCH=no` in the
  settings turns it off (open USB Link once to apply that).

## The network link (optional)

With a network link the Nano also shows up as a network adapter on the host, with
an address, and can run an SSH server on it. Pick the kind that suits the host in
a settings file, `USB-link/usblink.conf` on the Nano's drive (`/mnt/USB-link/` on
the Nano), one `KEY=value` per line:

| Setting | Values | Default |
|---|---|---|
| `NETWORK` | `no`: just ADB + the serial port (the stock setup)<br>`rndis`: for Windows PCs<br>`ncm`: for Linux hosts (other handhelds, Linux PCs) | `no` |
| `ADDRESS` | the Nano's address on the link, `a.b.c.d/prefix` | `169.254.170.5/16` for `rndis`, `10.170.0.5/24` for `ncm` |
| `SSH` | `auto`: start SSH if `authorized_keys` is there; `no`: never | `auto` |
| `WATCH` | `yes`: reconnect by itself after replugs (see above); `no`: only when you open it | `yes` |

Why two kinds:

- **Windows** loads its own RNDIS driver for it, no install needed (verified).
  Windows gives its side of the link a `169.254.x.x` address by itself, which is
  why the `rndis` default address is in that range: `ssh root@169.254.170.5`
  works with no network settings on the PC.
- **Windows can't use `ncm`** with this firmware: its NCM driver connects, but the
  Nano's kernel rejects every packet ("Wrong NDP SIGN" in `dmesg`).
- Linux hosts understand `ncm` (`cdc_ncm`), and most also `rndis` (`rndis_host`),
  but not all of them build in `rndis_host`. The host needs an address on the same
  network, for example `10.170.0.1/24` for the `ncm` default.

In network mode the serial port (`COMn` on Windows) is not there: the Nano's USB
controller can't run serial, ADB and a network link at once. ADB keeps its usual
interface number, so the PC's ADB driver keeps working.

### SSH

1. Put your **public** key (for example `id_ed25519.pub`) in
   `USB-link/authorized_keys` on the Nano's drive, or push it:
   `nanoctl push ~/.ssh/id_ed25519.pub /mnt/USB-link/authorized_keys`.
2. Set `NETWORK=rndis` or `NETWORK=ncm` and open USB Link.
3. `ssh root@169.254.170.5` (or the `ncm` address).

The server is `dropbear` from the firmware: key logins only, listening only on the
link address. Its host key is made on first use and kept in `USB-link/`, so the
Nano keeps the same identity. Copies over SSH run at about 0.6 MB/s: the Nano's
single CPU core is busy with the encryption.

## What it does

1. checks that the Nano is in ADB mode (else it says so and stops);
2. takes the USB gadget down completely, and stops `adbd`;
3. builds it again the way the firmware does at boot (`/usr/local/sbin/adb`): same
   USB ID and names, a fresh function filesystem, `adbd` started and ready, then
   the gadget is switched on. With a network link, the network function takes the
   serial port's place. **If the network version won't start, it builds the stock
   ADB + serial one instead**, so USB is never left dead (the notice then says
   "ADB ONLY: NETWORK FAILED");
4. waits up to 15 s for the host, then sets the address and starts SSH if asked;
5. starts the auto-reconnect watcher (unless `WATCH=no`) and shows the result.

Each run is logged to `/mnt/FunKey/usblink.log`, including the USB power, its
voltage and the connection state second by second while it waits. When a host
doesn't connect, that log is the first thing to read.

## Caveats

- **ADB mode only.** In storage mode it says "switch to ADB first".
- **Run it from the menu.** Started over `adb` it cuts its own `adb` session; start
  it detached there (`setsid sh usblink.sh </dev/null >/dev/null 2>&1 &`).
- **Some hosts still don't connect.** An Anbernic RG34XXSP (Knulli) with a USB-C to
  USB-A adapter sees the Nano at full speed only and gets no answer from it, so it
  gives up ("unable to enumerate" in its `dmesg`); the Nano's log shows power but
  no host. A Windows PC with the same cable works every time. Not solved yet.
- It lasts until the next reboot, auto-reconnect included. A reboot gives back the
  stock USB setup; open USB Link once to get both back.

## Test hooks

`usblink.sh` honours `USBLINK_GADGET`, `USBLINK_UDC_CLASS`, `USBLINK_DIR`,
`USBLINK_LOG`, `USBLINK_POWER`, `USBLINK_RUN` (pid files and the watcher's copy),
`USBLINK_TICK` and `USBLINK_SETTLE` (the watcher's timing), `USBLINK_DRYRUN=1`
(print the plan, change nothing; with `--watch`, log rebuilds instead of doing
them), `USBLINK_QUIET=1` (no on-screen notice) and `USBLINK_FORCE_FAIL=1` (make a
network gadget fail to bind, to test the fallback). `tests/test_usblink.sh` uses
them to test the decisions, and the watcher on fake status files, without
touching the real USB setup (which would cut its own `adb` session).
