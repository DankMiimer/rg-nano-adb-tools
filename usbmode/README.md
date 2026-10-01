# USB Mode shortcut

Two tiny OPKs for the Nano's **Applications** menu that switch it between **ADB**
and **USB storage**. Only one is visible at a time, and its name and icon say what
pressing it will do:

| Nano is in | Entry shown | Icon | Pressing it |
|---|---|---|---|
| ADB mode | Switch to USB | USB symbol | reboots into USB storage mode |
| storage mode | Switch to ADB | `>_ ADB` | reboots into ADB mode |

## Install

The Nano must be in ADB mode (the installer talks to it with `nanoctl`).

```bash
usbmode/build_usbmode_opk.sh --refresh
```

Needs `mksquashfs` (squashfs-tools) and `python3` (it draws the icons, no
imaging library needed). Other forms:

```bash
usbmode/build_usbmode_opk.sh --build-only   # just build SwitchToUSB.opk / SwitchToADB.opk
usbmode/build_usbmode_opk.sh                # install, but don't restart the menu
```

It installs the entry that matches the mode the Nano is in **now**, parks the
other as `SwitchTo*.opk.off` (the menu ignores that extension), and removes an
older single-entry `USBMode.opk` if present. `--refresh` restarts the menu so the
change shows immediately. The OPKs go to `/mnt/Applications`.

## What pressing it does

1. works out the current mode from the running USB gadget;
2. flips the `/mnt/adb` marker (see [../docs/FIRMWARE.md](../docs/FIRMWARE.md));
3. swaps which of the two entries is visible, by renaming files only (the running
   OPK is loop-mounted, so its file must never be rewritten), revealing the new one
   before hiding the old one so the menu is never left without an entry;
4. shows "SWITCHING TO ... / REBOOTING..." and reboots.

Each switch is also logged to `/mnt/FunKey/usbmode.log`.

## Caveats

- ADB mode hides the mass-storage drive.
- The label only follows switches made with this shortcut; re-run the installer
  after editing `/mnt/adb` by hand.
- It writes to `/mnt`; if the card is shared with a PC at that moment (storage mode
  with "Mount USB" active) the app can't run at all, because `/mnt` isn't mounted
  on the Nano.
- The notice layout (`^` = line break, leading spaces to centre) follows the stock
  shutdown/update notices on the Nano's 30-column screen.

## Test hooks

`usbmode.sh` honours `USBMODE_GADGET`, `USBMODE_MARKER`, `USBMODE_LOG`,
`USBMODE_APPS`, `USBMODE_DRYRUN=1` (no reboot) and `USBMODE_QUIET=1` (no on-screen
notice). `tests/test_usbmode.sh` uses them to test everything without changing the
real mode.
