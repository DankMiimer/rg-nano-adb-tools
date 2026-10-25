# How it works

Notes for anyone changing the tools. Everything here was verified on a real
Nano (see the tested-on line in the README).

## `run`: an old adbd, three workarounds

The Nano's `adbd` is an old build. Observed, from a Windows `adb.exe` 1.0.41:

| Quirk | Effect | Workaround |
|---|---|---|
| no shell protocol v2 | `adb shell 'exit 7'` returns 0 | the script runs in a subshell `( ... )`, then the device prints `__NANO_RC=$?`; `nanoctl` strips that marker and returns its value |
| the shell runs on a pty | with the Windows `adb.exe` every line ends in `\r\r\n` (observed) | CRs are stripped while the output is streamed |
| a pty shell never sees EOF on stdin | `printf x \| adb shell cat` hangs forever | `adb shell` always gets `</dev/null`; **no file data is ever piped through the shell** |

The subshell matters: without it `exit N` in your command would kill the shell
before it can print the marker. If there is no marker at all (the shell died:
reboot, unplug) the exit status falls back to adb's own.

## Files: `adb push` / `adb pull` only

Because nothing can be streamed through the shell, transfers use the sync
protocol (`adb push` / `pull`), which was verified byte-exact (SHA-256) on random
data. `push-text` strips CRs first (via a staged temporary copy).

Under WSL the USB device belongs to Windows, so `adb.exe` is used and needs
Windows paths: `wslpath -w` converts anything under `/mnt/<drive>/`. Files in the
Linux filesystem are copied through the **Windows temp folder** first
(`NANO_STAGE_DIR` overrides it) and removed afterwards. So are files whose Windows
path is 240+ characters long: `adb.exe` is not long-path aware and fails with
"cannot stat" at `MAX_PATH` (260), even for a file that exists. (`adb.exe` can often read
`\\wsl.localhost\...` paths too, but it is slower and less predictable.)
`cmd.exe` is only used to ask for `%TEMP%`; it isn't on a clean WSL `PATH`, so it
is also looked up under `/mnt/<drive>/Windows/System32`.

## `shot`

`fbgrab` (already on the Nano) writes `/dev/fb0` (240x240, RGB565) to a PNG in
`/tmp`; `nanoctl` pulls it and deletes it. It is the framebuffer content, so it
shows what an app drew, not whether the backlight is on.

## `dev`: pause the menu, don't disable it

The menu (`gmenu2x`) owns the screen and the buttons, and the `frontend` script
restarts it whenever it exits. To give an app the screen cleanly, `dev`:

1. checks that a menu process (`gmenu2x`/`retrofe`) is running, else refuses;
2. pauses it with `SIGSTOP`;
3. runs your app, output streamed back, with an optional watchdog timeout;
4. on exit, runs `termfix_all` and `keymap default` (what the supervisor does
   after an app), then **kills the menu** so the supervisor starts a fresh one.
   That redraws the screen and drops button presses that arrived while it was
   paused. (`SIGTERM` is sent after `SIGCONT`, because a stopped process would not
   act on it.)

The runner is a shell function sent to the Nano as text, so no helper file is left
behind, and it traps `HUP`/`INT`/`TERM`: if the PC-side process is killed or the
cable is pulled, the menu is restored by the Nano itself. This was tested by
killing the PC-side `adb` client mid-run.

**Why not `frontend set none`?** It disables the menu by creating
`/mnt/disable_frontend` on the SD card. If the run then dies (crash, flat battery,
yanked cable) the Nano **boots with no menu** until someone deletes that file.
`SIGSTOP` leaves nothing on the card, so a reboot always recovers.

Local `.opk` files are run with `opkrun <file>`, which is what the frontend calls
(`opkrun [-m metadata] OPK_FILE [ARGS]`); `/opk` is unmounted afterwards.

## USB Mode: marker, reboot, label swap

See [../usbmode/README.md](../usbmode/README.md). Two points worth knowing:

- The script decides the **current** mode from what the running gadget exposes
  (`/sys/kernel/config/usb_gadget/FunKey/functions/ffs.adb`), not from the marker,
  so a half-finished switch just completes instead of flipping back.
- It deliberately avoids `/run/rebooting`. Reading the `frontend` script,
  `/run/rebooting` plus `/mnt/last_opk` appears to make the frontend **resume the
  last app after the next boot**, which would relaunch the switcher and flip the
  mode again in a loop. That is inferred from the script, not tested (testing it
  risks exactly that boot loop). The stock Reboot entry is just `reboot`, and so
  is this; it also deletes `/mnt/last_opk` first.

## USB Link: rebuild the gadget, don't edit it

See [../usblink/README.md](../usblink/README.md). What it is built on, all seen on
a real Nano:

- **USB only works with a host that was there when the gadget was bound.** After
  an unplug the stock ADB gadget never comes back: on a replug the PC gets one
  `high-speed config` message in the Nano's `dmesg`, then the UDC state
  (`/sys/class/udc/musb-hdrc.1.auto/state`) drops to `not attached` and stays
  there, with USB power present. Other hosts see a full-speed device that doesn't
  answer ("unable to enumerate"). Only a reboot, or a rebuild, recovers it.
- **A full rebuild while the cable is in works.** Unbind, stop `adbd`, unmount the
  function filesystem, remove the gadget; then create it again, mount FunctionFS,
  start `adbd`, wait until it has written its descriptors (`ep1` appears), bind.
  That is the boot sequence of `/usr/local/sbin/adb`. The PC configures the gadget
  within about a second.
- **Editing the gadget under a running `adbd` does not.** Unbind, add a function,
  rebind: the bind succeeds, but no host ever sees the device again until a reboot.
- **The musb controller has 4 data endpoints each way.** Serial (ACM: interrupt IN
  + bulk IN/OUT) + ADB (bulk IN/OUT) + a network function (interrupt IN + bulk
  IN/OUT) needs 5 IN, and the bind fails with "No such device". So the network
  function replaces ACM. It is linked first, so it takes interfaces 0-1 like ACM
  did and ADB stays interface 2, which is what the PC's ADB driver was installed
  for (`VID_8087&PID_011E&MI_02`).
- **Windows can't use NCM with this kernel (4.14).** The UsbNcm driver binds and
  the adapter comes up, but `f_ncm` rejects every packet with "Wrong NDP SIGN".
  RNDIS works: with Microsoft OS descriptors (`os_desc`, compatible ID `RNDIS`,
  sub-ID `5162001`, as `usb_gadget` sets for its own `usbnet` mode) Windows loads
  "Remote NDIS Compatible Device" by itself. Windows only reads OS descriptors the
  first time it sees a USB ID + device version, and it has seen `8087:011e` version
  `0x0414` (the kernel's default) without them, so RNDIS mode reports `0x0415`.
- **USB-C to USB-C cables power nothing.** The Nano's port evidently lacks the CC
  pull-down resistors that tell a USB-C host a device is there, so neither a PC nor
  another handheld switches its 5 V on (`axp20x-usb/online` stays 0). USB-A to
  USB-C cables don't need them.
- **SSH without touching the read-only root.** `dropbear` only reads
  `~/.ssh/authorized_keys`, and `/root` is on the read-only partition. USB Link
  mounts a small tmpfs over `/root` holding a copy of what was there (`.profile`,
  the `.gmenu2x` link) plus the key from `/mnt/USB-link/`. It is gone at reboot.
