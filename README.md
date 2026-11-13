# Drum78's OS USB & WiFi Link

One app (**Connect**) to get your **RG Nano**, **GBA Mini**, or **FunKey S** online over USB or a Wi-Fi dongle, share files between handhelds, and switch USB modes — without touching system files or editing config files by hand.

Requires [DrUm78's FunKey-OS](https://github.com/DrUm78/FunKey-OS) (tested on builds reporting `FunKey-OS 2.3.0`, kernel `4.14.14-funkey` from 2026-01-18).

## Quick start

### 1. Install `Connect.opk`

1. Download **`Connect.opk`** from the [latest release](https://github.com/DankMiimer/drum78-os-usb-wifi-link/releases/latest).
2. Connect your handheld to your PC in **Mount USB** mode and copy `Connect.opk` into the **`Applications`** folder on the SD card.
3. Eject safely, reboot (or restart the menu), and open **Applications → Connect**.

### 2. Get online over USB (Windows or Linux PC)

1. Download **`Share-Internet.cmd`** and **`share-internet.ps1`** (for Windows, keep both in the same folder) or **`share-internet.sh`** (for Linux) from the [latest release](https://github.com/DankMiimer/drum78-os-usb-wifi-link/releases/latest).
2. On your handheld, open **Connect → USB**:
   - Set **Network** to **Windows** or **Linux**.
   - Set **Internet** to **on**.
   - If you are still in **USB Storage** mode, select **Mode** to switch to **ADB mode** (the handheld reboots once; you can switch back to USB Storage the same way anytime).
3. Plug the handheld into your PC with a **USB-A to USB-C** cable *(USB-C to USB-C cables do not power the RG Nano)* and press **Apply / Reconnect** in **Connect → USB**.
4. Share your PC's internet connection:
   - **Windows:** double-click **`Share-Internet.cmd`** and approve the Administrator prompt.
   - **Linux:** run `sudo ./share-internet.sh`.

After setup, unplugging and replugging the cable reconnects automatically until the next reboot.

### 3. Wi-Fi dongle, Handheld Link & USB mode switching

- **USB Wi-Fi Dongle (RG Nano only):** plug in an **RTL8188FU / RTL8188FTV** USB Wi-Fi adapter and open **Connect → Wi-Fi** to scan and connect. **Only the RG Nano supports a USB Wi-Fi dongle** — in DrUm78's kernel source, only the RG Nano enables USB OTG/host mode (`dr_mode = "otg"`), while FunKey S, GBA Mini, and Q36 Mini are peripheral-only (`dr_mode = "peripheral"`), so Connect hides the Wi-Fi option on them automatically.
- **Handheld Link (0.4.0):** bundled inside **Connect → Handheld Link** for linking and sharing with another handheld (from [handheld-usb-link](https://github.com/DankMiimer/handheld-usb-link)).
- **Switch USB Mode:** open **Connect → USB → Mode** anytime to reboot between **USB Storage** (for copying ROMs/files as a drive) and **ADB mode** (for USB internet, SSH, and `nanoctl`).

## Supported devices

| Handheld | Screen | Supported in Connect | Status |
|---|---|---|---|
| **RG Nano** | 240×240 | Wi-Fi, USB Link / Internet, Handheld Link, USB Mode | Tested on hardware |
| **GBA Mini** | 240×160 window | USB Link / Internet, Handheld Link, USB Mode | Tested on hardware |
| **FunKey S** | 240×240 | USB Link / Internet, Handheld Link, USB Mode | Tested offline |

## Good to know

- **Use a USB-A to USB-C cable** (or a USB-C to USB-A adapter on the host): a direct USB-C to USB-C cable gives the RG Nano no power.
- **ADB mode hides the SD card drive:** switch back via **Connect → USB → Mode** whenever you want the SD card to appear as a normal USB drive, or use `nanoctl push` / `pull` while in ADB mode.
- **One USB port:** on the RG Nano, Handheld Link waits for Wi-Fi to be off because the dongle and another handheld share the same USB-C port.
- More edge cases and fixes: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

---

## Advanced & developer info

Everything below is optional and meant for developers, command-line workflows, or building from source.

<details>
<summary><strong>Developer CLI (<code>nanoctl</code>, live <code>dev</code> loop, screenshots)</strong></summary>

`nanoctl` lets you develop on a FunKey-OS handheld in ADB mode over one USB cable (control **and** power) from Linux, macOS, or Windows + WSL2:

```text
$ nanoctl status
device:    0123456789abcdef
host:      FunKey  kernel 4.14.14-funkey
usb mode:  adb (now)
next boot: adb (/mnt/adb present)
battery:   Charging 94%

$ nanoctl dev --timeout 20 --shot 3 ./myapp.sh    # pause menu, run, screenshot, restore menu
$ nanoctl shot                                    # capture current framebuffer PNG
```

### Requirements & setup

- Handheld booted in **ADB mode** (either via **Connect → USB → Mode**, or by creating an empty file named `adb` in the root of the SD card's data partition and rebooting). See [docs/FIRMWARE.md](docs/FIRMWARE.md).
- Host: `bash` 4+, `awk`, `sed`, `tr`, `grep`, and Android platform-tools (`adb` / `adb.exe`). Optional: `squashfs-tools` and `python3` for building OPKs and running offline tests.
- Tested from Windows 11 + WSL2 (Ubuntu) using Windows `adb.exe` (1.0.41). Native Linux and macOS are supported in script logic but untested.

If `./nanoctl` isn't executable, run `bash nanoctl ...` or `chmod +x nanoctl`.

### Commands

| Command | What it does |
|---|---|
| `status` | device, USB mode now / after next boot, battery, memory, free space |
| `shell` | interactive shell |
| `run <cmd...>` | run a command with **the command's real exit status** |
| `push <local> <remote> [mode]` | copy a file to the handheld |
| `push-text <local> <remote> [mode]` | same, stripping `\r` (for scripts edited on Windows) |
| `pull <remote> <local>` | copy a file from the handheld |
| `shot [file.png]` | screenshot of the framebuffer; prints the saved path |
| `dev [opts] <app> [args]` | pause the menu, run an app, stream output, restore menu |
| `restart-menu` | restart the menu (also frees one left paused) |

### Using `dev`

```bash
nanoctl dev ./myapp.sh                 # local script/binary: staged in /tmp/nano_dev
nanoctl dev ./game.opk                 # local .opk: launched via opkrun
nanoctl dev /usr/bin/something         # binary/script already on the handheld
nanoctl dev --timeout 20 ./myapp.sh    # stop after 20 s
nanoctl dev --shot 3,10 ./myapp.sh     # save screenshots 3 s and 10 s after start
```

The menu owns the framebuffer, so `nanoctl dev` pauses (`SIGSTOP`) it while your app runs and restarts it afterward. Nothing is written to the SD card; even if the cable disconnects mid-run, a reboot recovers cleanly. See [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).

### `nanoctl` environment variables

| Variable | Default | Meaning |
|---|---|---|
| `NANO_ADB` | first `adb` that sees a device | path to `adb` / `adb.exe` |
| `NANO_SERIAL` | the only attached device | adb serial (`ANDROID_SERIAL` also works) |
| `NANO_SHOT_DIR` | `shots/` next to `nanoctl` | default directory for `shot` |
| `NANO_DEV_DIR` | `/tmp/nano_dev` (RAM disk) | staging directory for `dev` |
| `NANO_DEV_FRONTENDS` | `gmenu2x retrofe` | menu processes `dev` pauses |
| `NANO_STAGE_DIR` | Windows temp folder (WSL) | staging for files `adb.exe` can't read |

</details>

<details>
<summary><strong>Building OPKs from source, standalone apps &amp; manual <code>usblink.conf</code></strong></summary>

### Build and install Connect from source

With a handheld connected in ADB mode:

```bash
connect/build_connect_opk.sh --refresh      # build Connect.opk, push to SD card, restart menu
connect/build_connect_opk.sh --build-only   # build Connect.opk only
connect/build_connect_opk.sh --retire-old   # park WiFi, USBLink, SwitchToUSB/ADB as *.opk.off
```

- Binaries (kernel modules, `wpa_supplicant`, Python runtime) come from [`wifi/build_wifi_opk.sh`](wifi/build_wifi_opk.sh) and are built automatically if missing (see [wifi/README.md](wifi/README.md#building)).
- `HandheldLink.opk` is bundled automatically if present in a sibling `handheld-usb-link` checkout (`../handheld-usb-link/handheld-link/funkey/HandheldLink.opk`) or at `HLINK_OPK`.
- Component guides: [connect/README.md](connect/README.md), [usblink/README.md](usblink/README.md), [wifi/README.md](wifi/README.md), [usbmode/README.md](usbmode/README.md).

### Standalone OPKs

Each piece can also still be built separately if you only want one part:

```bash
usblink/build_usblink_opk.sh --refresh
usbmode/build_usbmode_opk.sh --refresh
wifi/build_wifi_opk.sh --refresh
```

### Manual USB network config (`USB-link/usblink.conf`)

Connect edits `USB-link/usblink.conf` automatically, or you can edit it by hand (`KEY=value` per line):

- `NETWORK=no` | `rndis` (Windows) | `ncm` (Linux)
- `ADDRESS=dhcp` (for host internet sharing) or a static CIDR (defaults: `169.254.170.5/16` for `rndis`, `10.170.0.5/24` for `ncm`)
- `DNS=1.1.1.1` (auto-set by Connect for Windows internet sharing)
- `SSH=auto` (starts key-only SSH when `USB-link/authorized_keys` exists) or `no`
- `WATCH=yes` | `no` (auto-reconnect after replugs)

PC sharing helpers support `-Status`, `-DryRun`, `-Yes`, `-Force`, and `-ConfigureNano` (`Share-Internet.cmd -Help` / `./share-internet.sh --help`). Logs are written to `/mnt/FunKey/usblink.log`.

</details>

<details>
<summary><strong>Streaming apps, technical limits, tests &amp; prior art</strong></summary>

### YouTube and NRK streaming apps

**NanoPlay** (YouTube) and **NRK** TV/radio apps are documented in [streaming/](streaming/README.md) with source, build instructions, and an [SD-card download](https://github.com/DankMiimer/drum78-os-usb-wifi-link/releases/tag/streaming-2026.11.07). They require internet access and `Applications/MPlayer.opk` (NRK TV also requires your own stream key).

### Technical limits & known issues

- USB mode (`/mnt/adb`) is only read at boot, so every switch between ADB and USB storage is a reboot.
- After unplugging the cable, USB does not re-enumerate until a reboot unless Connect / USB Link is running.
- `nanoctl run` cannot read stdin, and nothing can be piped into `adb shell` on this firmware (`adbd` never sees EOF). Use `nanoctl push` / `push-text`.
- `nanoctl dev` kills the main process it started, not detached child processes.
- `nanoctl shot` captures the framebuffer (what was drawn), not backlight state.
- Standalone `SwitchToUSB` / `SwitchToADB` labels only track switches made by the script itself. If you toggle `/mnt/adb` manually, re-run `usbmode/build_usbmode_opk.sh --refresh` (or use **Connect → USB → Mode**).
- Inside WSL2, Linux `adb` cannot see USB devices owned by Windows; `nanoctl` picks the first `adb` that sees a device (usually Windows `adb.exe`).

### Tests

Offline unit tests (`test_connect.py`: 44 tests, `test_wifi_backend.py`: 13 tests):

```bash
python3 tests/test_connect.py
python3 tests/test_wifi_backend.py
```

Full on-device integration tests (requires a real handheld in ADB mode, idle at the menu; flashes the screen and restarts the menu a few times without changing USB mode or files):

```bash
tests/run_all.sh
```

### Prior art

- **ADB mode** on these devices comes from [DrUm78's FunKey-OS](https://github.com/DrUm78/FunKey-OS/releases/tag/FunKey-OS-DrUm78) (creating `/mnt/adb` and rebooting).
- **SSH over USB networking** is covered in the [official FunKey docs](https://doc.funkey-project.com/developer_guide/tutorials/ssh_connection/).
- **Running apps from shell** requires pausing/stopping the menu so `opkrun` can acquire the video mode.

Related tools: [FunKey SDK guide](https://doc.funkey-project.com/developer_guide/tutorials/build_system/build_program_using_sdk/), [rgnano_sample_rs](https://github.com/bluexe203/rgnano_sample_rs), [funkeymonkey](https://github.com/bzar/funkeymonkey).

</details>

## Credits & license

Built on the work of the [FunKey Project](https://github.com/FunKey-Project) and [DrUm78's FunKey-OS fork](https://github.com/DrUm78/FunKey-OS). Not affiliated with Anbernic, the FunKey Project, or DrUm78.

Released under the [MIT License](LICENSE).
