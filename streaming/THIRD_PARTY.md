# Streaming app third-party components

The release ZIP retains the installed NanoPlay.opk, NRK.opk and app payloads.
The repository MIT license applies to project-authored code, not all bundled
components. Full notices are in `licenses/` beside this file in the release ZIP.

| Component | Version / source | License / notice |
|---|---|---|
| CPython | 3.12.12, [source](https://www.python.org/downloads/release/python-31212/) | PSF and incorporated notices, `CPYTHON-LICENSE.txt` |
| OpenSSL | 1.1.1w, [source](https://github.com/openssl/openssl/releases/tag/OpenSSL_1_1_1w) | OpenSSL / SSLeay, `OPENSSL-LICENSE.txt` |
| QuickJS | 2025-04-26, [source](https://bellard.org/quickjs/) | MIT, `QUICKJS-LICENSE.txt` |
| yt-dlp | 2026.10.26, [source](https://github.com/yt-dlp/yt-dlp/tree/2026.10.26) | Unlicense, plus vendored components; `YT-DLP-LICENSE.txt` and `YT-DLP-NOTICES.txt` |
| yt-dlp EJS | bundled with the upstream yt-dlp zipapp, [source](https://github.com/yt-dlp/ejs) | Unlicense, `YT-DLP-EJS-LICENSE.txt` |
| st-sdl | fallback terminal from RG Nano firmware, [source](https://github.com/DrUm78/st-sdl) | MIT/X Consortium, `ST-SDL-LICENSE.txt` |
| tiny-AES-c based decryptor | `nrk/aesdec.c`, [upstream](https://github.com/kokke/tiny-AES-c) | Public domain / Unlicense, `TINY-AES-UNLICENSE.txt` |
| Raster Forge | GGBotNet, [author's font page](https://ggbot.itch.io/raster-forge-font) | CC0 1.0, `CC0-1.0.txt`; installed filename `RasterForgeRegular-JpBgm.ttf` |
| Mozilla CA certificate bundle | `rt/etc/cacert.pem` inside NanoPlay.opk, [curl CA extract](https://curl.se/docs/caextract.html) | MPL 2.0, `MPL-2.0.txt` |

The fallback st dynamically uses the firmware's libc and SDL. MPlayer.opk is a
separate firmware dependency and is not bundled. No video downloads, personal
cookies, watch/search history, Wi-Fi settings, private device identifiers or NRK
stream keys are included.

The runtime's sources and build procedure are pinned in
`wifi/build_wifi_opk.sh`. The installed OPKs are preserved as exported, with
these license notices alongside them in the ZIP rather than repacking the OPKs.
