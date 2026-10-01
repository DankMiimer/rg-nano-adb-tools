# Troubleshooting

## `nanoctl: no adb device found`

First work out which USB mode the Nano is in. On Windows, Device Manager / the
USB device ID tells you:

| You see | Mode | What to do |
|---|---|---|
| "FunKey S Shared Disk" / ID `1d6b:0104` | storage mode | switch to ADB: **Switch to ADB** on the Nano, or put an empty `adb` file in the drive's root and **reboot the Nano** |
| "ADB Interface" + "USB Serial Device (COMn)" / ID `8087:011e` | ADB mode | `adb kill-server`, then `adb devices`; see the driver note below |
| nothing | not connected / no data | try another cable (some are charge-only) and port |

Remember the mode is read only at boot: **replugging does not switch it.**

If you never get ADB after a reboot, your firmware may not support it:
[FIRMWARE.md](FIRMWARE.md).

## Windows shows the ADB device with a warning, or `adb devices` is empty

Windows normally installs the "ADB Interface" driver by itself. If it didn't,
install the Google USB Driver or a "Universal ADB driver", then
`adb kill-server && adb devices`. The serial port (`COMn`) that appears next to it
is the Nano's ACM function; `nanoctl` doesn't use it.

## Under WSL: "no adb device found" although Windows sees the Nano

A Linux `adb` installed inside WSL cannot see USB devices that Windows owns.
`nanoctl` picks the first `adb` that **does** see a device, so install the Windows
platform-tools (`winget install Google.PlatformTools`) and it will use
`adb.exe`. Force one with `NANO_ADB=/mnt/c/.../adb.exe`. If you run `adb.exe`
directly, the adb server it starts belongs to Windows too, which is what you want.

## "No Media" / the drive is empty in storage mode

In storage mode the card is only shared after you pick **Mount USB** on the Nano.
Until then Windows shows a drive with no media.

## The screen shows my app but I can't get the menu back

`dev` normally restores the menu itself, even if the PC side dies. If it was left
paused anyway:

```bash
nanoctl restart-menu
```

Nothing is stored on the card, so a reboot also always recovers.

## `dev` says "no menu ... is running"

Another app (a game, a previous test) owns the screen. Exit it first, or pass
`--force` if you know what you are doing. If your menu process has another name,
set `NANO_DEV_FRONTENDS`.

## "Switch to ..." shows the wrong name or icon

The label follows switches made with the shortcut. After changing `/mnt/adb` by
hand, re-sync it:

```bash
usbmode/build_usbmode_opk.sh --refresh
```

## `run` or a pipe hangs

Never pipe data into `adb shell` on this firmware: the shell never sees
end-of-input. `nanoctl run` already uses `</dev/null`. To get data onto the Nano use
`nanoctl push`.

## Several devices attached

```bash
NANO_SERIAL=<serial from adb devices> nanoctl status
```

## Running the tests and the screen flashes / the menu restarts

That's expected: the tests draw colours to check screenshots and restart the menu.
They never change the real USB mode.
