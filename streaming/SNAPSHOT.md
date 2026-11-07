# Installed snapshot validation

Exported on 2026-11-07 from an RG Nano running DrUm78 FunKey-OS 2.3.0,
kernel 4.14.14-funkey (2026-01-18 build). App files were read over ADB.
No app launch, reinstall, menu restart, USB-mode change or firmware change was
performed. Live service playback was not retested.

- All shell scripts and the NanoPlay Python engine passed syntax checks.
- 1077 payload checksums verified; 1059 app/launcher/extractor files match the device export byte for byte.
- ZIP has no stream keys, cookies, logs, history or cache folders.
- Installed sourceless yt-dlp imports under Python 3.12 and reports 2026.10.26.
- Decryptor source passed an AES-128-CBC round trip against OpenSSL with synthetic data.
- A full source build of both OPKs and the ZIP completed successfully in WSL; no device installation or playback was performed.

Both engines also matched the corresponding working source on the PC byte for
byte. The release preserves the installed OPKs, decryptor and extractor.

| Exported file | SHA-256 |
|---|---|
| `NanoPlay.opk` | `a0c281bcf5f445bff51eca20a40262c94153b1a35d7104b791a06dafbf373e34` |
| `NRK.opk` | `9ddfee74e5f40583cf85b5efb641c6dce81d196665aad8773167722420aa94de` |
| `youtube/youtube.sh` | `fed1efc20382bae6ebdfb0c07f90818be40a06a570b05515c2efa75384542751` |
| `youtube/youtube_nano.py` | `8f97c91a1767282cff2efe5a36a3ed65e32e352de98988cf7f694e7d93dcd95e` |
| `nrk/nrklive.sh` | `0a7d461553803e770f75df46cc4b62e7a5d9118ed4976374b63a3dbad85492ee` |
| `nrk/aesdec` | `e1970c8129edcfad832477dd77d07f6bef57fbe85f574cb22944b78c2689027b` |

The ZIP contains a full per-file checksum manifest. Keys, cookies, logs, watch
and search history, and caches were excluded. MPlayer.opk is a separate
dependency. NRK TV requires a locally supplied stream key.
