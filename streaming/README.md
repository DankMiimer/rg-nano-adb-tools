# NanoPlay (YouTube) and NRK streaming apps

The apps installed on the owner's RG Nano, exported on **2026-11-07**. NanoPlay
is a terminal YouTube browser with search, favorites, watch later, history and
MPlayer playback. NRK is a live tuner for NRK1, NRK2, NRK3 and radio P1/P2/P3.
The engines, launcher scripts and artwork here match that installed snapshot.

## Download and install

Download **rg-nano-streaming.zip** from the
[streaming release](https://github.com/DankMiimer/rg-nano-adb-tools/releases/tag/streaming-2026.11.07).
An OPK alone is insufficient: both apps also need their files under `FunKey/`.

Requirements:

- An RG Nano with DrUm78's FunKey-OS firmware. The export came from a device
  reporting FunKey-OS 2.3.0, kernel `4.14.14-funkey` (2026-01-18 build).
- **`Applications/MPlayer.opk`** already installed on the card. It is a separate
  dependency and is not redistributed in this download.
- Internet via the [Wi-Fi dongle app](../wifi/README.md), or
  [USB Link and host internet sharing](../usblink/README.md#internet-over-the-cable).
- For **NRK TV**, your own valid 16-byte `content.key` in `FunKey/nrk/`.
  The device's key is deliberately excluded. Radio does not use a key.

1. Back up existing `FunKey/youtube`, `FunKey/nrk`, `Native games/NanoPlay.*`
   and `Native games/NRK.*` if installed. Close the apps before replacing files.
2. Extract the ZIP. **Merge the contents of `sd-card/` into the data partition's
   root**, keeping the folder names exactly as supplied. Preserve existing
   cookies, keys and `youtube/data/`; the ZIP supplies none of those files.
3. NRK's stock launcher starts on NRK1, so TV requires `FunKey/nrk/content.key`.
   For radio without a TV key, rebuild the NRK launcher after changing the final
   argument in `nrk/run.sh` from `nrk1` to `p1`. Do not publish your key.
4. Eject the card/USB storage cleanly. Open **NanoPlay** or **NRK** in the menu.
   Both OPKs go only in `Native games/`, avoiding duplicate entries.

Over ADB, extract the ZIP on the PC, close the apps and merge the same two
folders (these commands replace app files; they do not erase existing state):

```bash
adb push sd-card/FunKey/youtube /mnt/FunKey/
adb push sd-card/FunKey/nrk /mnt/FunKey/
adb push 'sd-card/Native games/NanoPlay.opk' '/mnt/Native games/NanoPlay.opk'
adb push 'sd-card/Native games/NanoPlay.png' '/mnt/Native games/NanoPlay.png'
adb push 'sd-card/Native games/NRK.opk' '/mnt/Native games/NRK.opk'
adb push 'sd-card/Native games/NRK.png' '/mnt/Native games/NRK.png'
```

On this firmware `/media` aliases `/mnt`. Both engines mount the separate
`/media/Applications/MPlayer.opk` at `/tmp/mp` when needed.
NanoPlay.opk includes Python 3.12, QuickJS, OpenSSL, a CA bundle and a fallback
st terminal; no custom root filesystem is required.

## Controls

NanoPlay: D-pad moves, A selects, B goes back. Search and Play URL provide an
on-screen keyboard. Favorites/history/watch later are stored in `youtube/data/`.
During playback A pauses, B quits and Up/Down changes volume. The OPK alternates
between the st menu and standalone MPlayer so they do not share the framebuffer.

NRK: Left/Right cycles NRK1 -> NRK2 -> NRK3 -> P1 -> P2 -> P3; Up/Down changes
volume, A pauses, Menu quits. Radio displays a channel badge and available ICY
now-playing metadata.

## Limits of this snapshot

This is an export of the installed apps, with no new live playback certification.
Packaging, source syntax, extractor import/version and archive contents were
checked on the PC. The connected Nano was read only; no apps were launched or
reinstalled during publication.

YouTube extraction depends on the service and may take time on the Nano's single
core and 64 MB RAM. The bundled extractor is **yt-dlp 2026.10.26**, precompiled
for Python 3.12. The current player targets a combined H.264/AAC stream up to
360p, scaled to 240x135; unavailable formats, login requirements and service
changes can prevent playback. The in-app updater is inherited from the device;
rebuilding with a reviewed extractor version is preferable for this sourceless
package. Cookies, if needed, belong only in your local `FunKey/youtube/cookies.txt`.

NRK uses HTTP playlists/segments and a locally supplied AES-128 HLS key for TV.
Availability depends on NRK's current URLs, key and location restrictions. A
missing/expired key prevents TV playback; this export does not distribute keys
or work around restrictions. The launcher starts on TV by default. The existing
engine can retry unavailable streams indefinitely. Power-menu exit/resume and
other firmware builds were not revalidated for this release.

## Build from source

In Linux or WSL Ubuntu, install `squashfs-tools`, `curl` and **Python 3.12**, and
provide the FunKey SDK. Build the shared Python/QuickJS runtime using
`wifi/build_wifi_opk.sh --build-only` first (see its requirements), then:

```bash
bash streaming/build_bundle.sh /path/to/output
```

This only builds a ZIP; it never installs files, restarts the menu or changes the
USB mode. It compiles `nrk/aesdec.c`, downloads and SHA-256 checks the pinned
upstream yt-dlp zipapp, precompiles it, and builds the two launchers. Artwork and
radio badge clips are included as assets, so ffmpeg is not needed.

`NANO_PY_RUNTIME` selects another Python 3.12 runtime; `FUNKEY_SDK` or `NANO_CC`
selects the ARM compiler. The RG Nano firmware provides `/usr/bin/st`; `NANO_ST`
can supply a copy for inclusion on firmwares without it. The source build omits
that fallback unless supplied.

For an exact installed-package export, `NANOPLAY_OPK`, `NRK_OPK`, `AESDEC_BIN`
and `YTDLP_DIR` accept the already installed OPKs, decryptor and `yt-dlp.d/`
directory. Inputs must be trusted and free of private state. The published ZIP
uses this path, preserving both OPKs and the installed runtime/extractor.
Every payload file is listed in the ZIP's `SHA256SUMS.txt`; a separate checksum
covers the ZIP. A rebuild from source need not produce identical OPK bytes.

## Files and credits

`youtube/` contains the NanoPlay engine, two-phase OPK launcher and icon.
`nrk/` contains the NRK engine, launcher, decryptor source, font and badge videos.
The project-authored scripts are covered by the repository's [MIT license](../LICENSE).
Bundled third-party components keep their own licenses: see
[THIRD_PARTY.md](THIRD_PARTY.md) and `licenses/`. YouTube, NRK and their content
remain the property of their respective owners; this project is unaffiliated.
