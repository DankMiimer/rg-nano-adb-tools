# Firmware requirements: does my Nano support ADB?

`nanoctl` needs the Nano to expose an **ADB interface** over USB. That is a
feature of the firmware, not of the hardware, and not every RG Nano firmware has
it.

## How ADB mode works on FunKey OS

FunKey OS builds the Nano's USB personality **once, at boot**. The `S10share`
init script runs `share init`, which sources `usb_gadget`; `usb_gadget` looks for
marker files on the SD card's data partition (mounted as `/mnt` on the Nano, and
shown as a drive when you mount USB storage from a PC):

| Marker in the root of the data partition | USB mode after the next boot | USB ID |
|---|---|---|
| none | mass storage ("FunKey S Shared Disk") | `1d6b:0104` |
| `usbnet` | mass storage + RNDIS Ethernet | `1d6b:0104` |
| `adb` | ADB + a serial (ACM) port; **no mass storage** | `8087:011e` |

The table is read from the scripts; only the "none" and `adb` rows were actually
exercised on a device (the `usbnet` row is untested here, and on at least one
Windows machine the RNDIS driver failed to install). `adb` wins if both are
present. In ADB mode the Nano still charges from the PC.
On the Nano, `/usr/local/sbin/adb start|stop` and `/usr/bin/adbd` do the work.

Because the markers are read only at boot, **replugging the cable changes
nothing; the Nano must reboot.**

## Where this comes from

The `usb_gadget` and `adb` scripts are in the upstream
[FunKey-OS](https://github.com/FunKey-Project/FunKey-OS) repository
(`FunKey/board/funkey/rootfs-overlay/usr/local/sbin/`). The ADB support was added
to upstream master on 2022-01-01 ("add ADB to FunKey-OS"), which is **after** the
last tagged release I could find (2.3.0, June 2021). So:

- the official tagged releases (the last is 2.3.0) do **not** have it;
- a build made from FunKey-OS master after that commit does, if it also ships the
  `adbd` binary;
- forks vary, and **the version number does not tell you**. The build `nanoctl`
  was developed on reports `FunKey-OS 2.3.0` in `/etc/os-release` (and in the login
  banner), yet it includes ADB: it is
  [DrUm78's FunKey-OS fork](https://github.com/DrUm78/FunKey-OS) (kernel
  `4.14.14-funkey`, built 2026-01-18), whose repository contains the `adb` script.
  That repository was archived (read-only) in April 2026.

## Find out whether yours supports it

You usually have no shell on the Nano to look, so just try. It is harmless:

1. Plug the Nano in and mount USB storage so it appears as a drive.
2. Create an **empty file named `adb`** (no extension) in the root of that drive.
   Windows: make sure file extensions are shown so it isn't `adb.txt`.
3. Eject the drive, then **reboot the Nano** (not just unplug it).
4. Run `adb devices` (or `nanoctl status`).

| What you see after the reboot | Meaning |
|---|---|
| a device listed, USB ID `8087:011e` | ADB works |
| still "FunKey S Shared Disk" / `1d6b:0104` | the firmware ignores the marker: no ADB support |
| ADB interface present but `adb devices` is empty | driver problem, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |

On firmware without support the extra file is simply ignored; delete it again if
you like.

## Getting out of ADB mode

Once in ADB mode there is no drive on the PC. Either use the **USB Mode**
shortcut on the Nano (see [../usbmode/README.md](../usbmode/README.md)), or:

```bash
nanoctl run 'rm /mnt/adb'
```

and reboot the Nano.

## Adding ADB to a firmware you build

You need the `adbd` binary in the root filesystem (for Buildroot, an Android
tools package that provides `adbd`) and the upstream `usb_gadget` and `adb`
scripts from the commit above, plus the kernel gadget options they rely on
(configfs gadgets with FunctionFS and ACM). I have not built that myself, so
follow the upstream commit rather than this page.
