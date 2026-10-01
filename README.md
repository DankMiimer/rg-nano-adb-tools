# rg-nano-adb-tools

Develop on an **Anbernic RG Nano** over one USB cable: a real shell, file
transfer, screenshots, a safe "run my app and give me the menu back" loop, and
an on-device shortcut that flips the Nano between ADB and USB-storage mode.

The RG Nano has a single USB-C port. A WiFi dongle needs that port, so you can't
charge while using it. Over ADB the cable gives you control **and** power.

```text
$ nanoctl status
device:    0123456789abcdef
host:      FunKey  kernel 4.14.14-funkey
usb mode:  adb (now)
next boot: adb (/mnt/adb present)
battery:   Charging 94%

$ nanoctl dev --timeout 20 --shot 3 ./myapp.sh    # pause menu, run, screenshot, restore menu
$ nanoctl shot                                    # what is on the screen right now?
```

## Requirements

**The Nano must run a FunKey OS build that supports ADB.** Many do not: ADB was
only added to FunKey OS after its last tagged release, and the version number
won't tell you (the build tested here reports `FunKey-OS 2.3.0` and has it). See
[docs/FIRMWARE.md](docs/FIRMWARE.md) for how it works and how to find out
whether yours does (it takes one reboot to try).

Host:

- `bash` 4+, `awk`, `sed`, `tr`, `grep` and Android platform-tools (`adb`)
- optional: `squashfs-tools` and `python3` to build the USB Mode shortcut,
  `python3` for the tests

Tested on: an RG Nano running [DrUm78's FunKey-OS fork](https://github.com/DrUm78/FunKey-OS)
(the device reports `FunKey-OS 2.3.0`; kernel `4.14.14-funkey`, built 2026-01-18),
from Windows 11 + WSL2 (Ubuntu) using the Windows platform-tools `adb.exe`
(1.0.41). Native Linux and macOS hosts are written for but **untested**; reports
welcome.

## Quick start

1. Make the Nano boot in ADB mode: plug it in, mount USB storage, create an empty
   file named `adb` in the root of the card's data partition, eject, then
   **reboot the Nano** (replugging the cable is not enough).
   Details and caveats: [docs/FIRMWARE.md](docs/FIRMWARE.md).
2. Check it:

   ```bash
   ./nanoctl status
   ```

3. Install the on-device mode switch (optional but handy):

   ```bash
   usbmode/build_usbmode_opk.sh --refresh
   ```

   If `./nanoctl` isn't executable, run `bash nanoctl ...` or `chmod +x nanoctl`.

## Commands

| Command | What it does |
|---|---|
| `status` | device, USB mode now / after next boot, battery, memory, free space |
| `shell` | interactive shell |
| `run <cmd...>` | run a command; **the exit status is the command's** |
| `push <local> <remote> [mode]` | copy a file to the Nano |
| `push-text <local> <remote> [mode]` | same, stripping CRs (scripts edited on Windows) |
| `pull <remote> <local>` | copy a file from the Nano |
| `shot [file.png]` | screenshot of the Nano's screen; prints the saved path |
| `dev [opts] <app> [args]` | pause the menu, run an app, stream its output, restore the menu |
| `restart-menu` | restart the menu (also frees one left paused) |

### `dev`

```bash
nanoctl dev ./myapp.sh                 # a local script or binary: pushed to /tmp/nano_dev, then run
nanoctl dev ./game.opk                 # a local .opk: run with opkrun, the way the menu launches it
nanoctl dev /usr/bin/something         # a path or command that already exists on the Nano
nanoctl dev --timeout 20 ./myapp.sh    # kill it after 20 s if it hangs
nanoctl dev --shot 3,10 ./myapp.sh     # screenshots 3 s and 10 s after it starts
```

The menu owns the screen, so it is paused (`SIGSTOP`) while your app runs and
restarted afterwards, which redraws the screen and drops stale button presses.
The exit status is your app's. It refuses to run if no menu is running (another
app may own the screen) unless you pass `--force`. Nothing is written to the
SD card, so even if the PC or the cable dies mid-run a reboot recovers the Nano;
see [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).

### The USB Mode shortcut

`usbmode/` builds two tiny OPKs for the **Applications** menu. Only one is
visible at a time, and its name and icon say what pressing it will do:

| Nano is in | Entry shown | Pressing it |
|---|---|---|
| ADB mode | **Switch to USB** (USB symbol) | reboots into USB storage mode |
| storage mode | **Switch to ADB** (`>_ ADB`) | reboots into ADB mode |

You need it because the PC can't change the mode once the Nano is in storage
mode (there is no shell then). More in [usbmode/README.md](usbmode/README.md).

## Limits and known issues

- ADB mode **hides the mass-storage drive**: use `push`/`pull` instead. The card
  can't be shared with the PC while the Nano is using it.
- The mode is read **only at boot**, so every switch is a reboot.
- `run` cannot read stdin, and nothing can be piped into `adb shell` on this
  firmware (the shell never sees end-of-input). Use `push`.
- `dev` kills your app's main process, not children it spawned.
- Screenshots show the framebuffer (what was drawn), not the backlight state.
- The USB Mode label only follows switches made with the shortcut. If you change
  `/mnt/adb` by hand, re-run `usbmode/build_usbmode_opk.sh --refresh`.
- A Linux `adb` inside WSL cannot see USB devices that Windows owns; `nanoctl`
  picks the first `adb` that actually sees one, which will be `adb.exe`.

More: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Configuration

All optional, via environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `NANO_ADB` | first `adb` that sees a device | path to `adb` / `adb.exe` |
| `NANO_SERIAL` | the only attached device | adb serial (`ANDROID_SERIAL` also works) |
| `NANO_SHOT_DIR` | `shots/` next to `nanoctl` | where `shot` saves by default |
| `NANO_DEV_DIR` | `/tmp/nano_dev` (RAM disk) | where `dev` stages local apps |
| `NANO_DEV_FRONTENDS` | `gmenu2x retrofe` | menu process names `dev` pauses |
| `NANO_STAGE_DIR` | Windows temp folder (WSL) | staging for files `adb.exe` can't read |

## Tests

```bash
tests/run_all.sh
```

Integration tests against a **real** Nano in ADB mode, idle at the menu. The screen
flashes red/blue and the menu restarts a few times. The USB mode, your menu
entries and your files are not changed (the mode-switch tests are dry runs with
everything redirected to `/tmp`, and show no notices).

## Credits

Built on the work of the [FunKey Project](https://github.com/FunKey-Project) and
[DrUm78's FunKey-OS fork](https://github.com/DrUm78/FunKey-OS) for the RG Nano,
which these tools were developed against. This project is not affiliated with
Anbernic, the FunKey Project or DrUm78.

## License

[MIT](LICENSE).
