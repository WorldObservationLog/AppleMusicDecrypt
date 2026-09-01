# AppleMusicDecrypt

Apple Music decryption tool, inspired by [zhaarey/apple-music-alac-atmos-downloader](https://github.com/zhaarey/apple-music-alac-atmos-downloader)

Discussion Group: https://t.me/apple_music_alac 

# About V3
This branch is a rewrite built around the modern [WorldObservationLog/wrapper](https://github.com/WorldObservationLog/wrapper)
`lite` HTTP API and the [WorldObservationLog/Temari](https://github.com/WorldObservationLog/Temari)
local decryption library:

- **HTTP instead of gRPC** — the client talks to a wrapper/lite instance over plain JSON HTTP (`/m3u8 /key /lyrics /webplayback /license /status`).
- **Local streaming decryption (边下边解, default)** — the decrypt template is fetched once per song from `/key` and every sample is decrypted **locally** by the bundled `temari` Rust cdylib while the media file is still downloading. No per-sample round-trips to the wrapper, so throughput scales with your CPU cores.
- **Zero external binaries** — the fMP4 file is parsed, decrypted and re-encapsulated by a pure-Python ISO-BMFF module; tags are written with `mutagen`; integrity is verified structurally.
- **Batch-friendly** — prefetch key template reuse, cached metadata, streaming download with byte-range resume, per-fragment pipelined decryption, and a shared keep-alive CDN connection pool.
- **Music videos** — `dl <music-video-url>` downloads the MV: video + audio streams are fetched, decrypted with pure-Python AES-CBC cbcs Widevine and remuxed into one MP4 by the pure-Python muxer.
- **Full-screen TUI** — a responsive terminal interface with a live log pane, a tree-structured task sidebar (album → tracks), a command input with history/completion, a floating batch-URL panel and a status bar. Falls back to stacked single-pane mode on narrow terminals (phones / Termux).

# Usage

```shell
# Download song/album with default codec (alac)
download https://music.apple.com/jp/album/nameless-name-single/1688539265
# Or a shorter command
dl https://music.apple.com/jp/album/nameless-name-single/1688539265
# Download song/album with specified codec
dl -c aac https://music.apple.com/jp/song/caribbean-blue/339592231
# Overwrite existing files
dl -f https://music.apple.com/jp/song/caribbean-blue/339592231
# Specify song metadata language
dl -l en-US https://music.apple.com/jp/album/nameless-name-single/1688539265
# Download specify artist's all albums
dl https://music.apple.com/jp/artist/%E3%83%88%E3%82%B2%E3%83%8A%E3%82%B7%E3%83%88%E3%82%B2%E3%82%A2%E3%83%AA/1688539273
# Download specify artist's all songs
dl --include-participate-songs https://music.apple.com/jp/artist/%E3%83%88%E3%82%B2%E3%83%8A%E3%82%B7%E3%83%88%E3%82%B2%E3%82%A2%E3%83%AA/1688539273
# Download all songs of specified playlist
dl https://music.apple.com/jp/playlist/bocchi-the-rock/pl.u-Ympg5s39LRqp
# Download multiple songs with the same options, without retyping the command
dl -c aac -l en-US -b
https://music.apple.com/jp/playlist/bocchi-the-rock/pl.u-Ympg5s39LRqp
https://music.apple.com/jp/album/nameless-name-single/1688539265
# Download multiple songs in one line
dl https://music.apple.com/jp/playlist/bocchi-the-rock/pl.u-Ympg5s39LRqp https://music.apple.com/jp/album/nameless-name-single/1688539265
# Download a music video
dl https://music.apple.com/jp/music-video/1800449196
# Check the available quality of the song
quality https://music.apple.com/jp/playlist/bocchi-the-rock/pl.u-Ympg5s39LRqp
# Or a shorter command
qa https://music.apple.com/jp/playlist/bocchi-the-rock/pl.u-Ympg5s39LRqp
# You can hide a column by enabling it in the options.
qa --codec-id https://music.apple.com/jp/playlist/bocchi-the-rock/pl.u-Ympg5s39LRqp
# When you add --invert, it works the opposite way, showing the column for whichever option you enable.
qa --invert --codec-id https://music.apple.com/jp/playlist/bocchi-the-rock/pl.u-Ympg5s39LRqp
# Clear finished tasks from the sidebar
cl
```

# The TUI

The whole interface is a full-screen terminal application with three areas: a live log pane, a task sidebar showing downloads as a tree, and a command input bar with a status bar underneath.

## Command input
* `Enter` runs the command (Tab completes; ↑/↓ walk history; Home/End jump).
* Batch mode (`dl -b`) opens a floating panel — type one URL per line, `Ctrl+D` submits, `Esc` cancels.

## Keys
| Key | Action |
|-----|--------|
| `Tab` | move focus between log pane and input bar |
| `↑ / ↓ / PgUp / PgDn` | scroll the log (focus on log pane) |
| `End` | re-enable log auto-follow after scrolling |
| `F1` | help |
| `F2` | narrow terminals only: toggle LOG ↔ TASKS full-width panes |
| `F10` / `Ctrl+C` | exit (press twice when tasks are running) |
| `Ctrl+D` | submit the batch panel (batch mode) |
| `Esc` | cancel the batch panel |

Mouse is supported: click a pane to focus it, wheel to scroll.

## Task sidebar
Tasks are shown as a tree: an album / playlist / artist download creates a parent node; each song appears underneath with a live status icon (⏳ waiting, 🔄 running, ✅ done, ✔ already existed, ❌ failed, ⚠ some failed) and downloaded/decrypted byte counters. Music videos appear as 🎬 nodes.

# Support Codec

- `alac (audio-alac-stereo)`
- `ec3 (audio-atmos / audio-ec3)`
- `ac3 (audio-ac3)`
- `aac (audio-stereo)`
- `aac-binaural (audio-stereo-binaural)`
- `aac-downmix (audio-stereo-downmix)`
- `aac-legacy (audio-stereo, non-lossless audio, Widevine — pure-Python decrypt)`

# Support Link

- Apple Music Song Share Link (https://music.apple.com/jp/album/%E5%90%8D%E3%82%82%E3%81%AA%E3%81%8D%E4%BD%95%E3%82%82%E3%81%8B%E3%82%82/1688539265?i=1688539274)
- Apple Music Album Share Link (https://music.apple.com/jp/album/nameless-name-single/1688539265)
- Apple Music Song Link (https://music.apple.com/jp/song/caribbean-blue/339592231)
- Apple Music Artist Link (https://music.apple.com/jp/artist/%E3%82%A8%E3%83%B3%E3%83%A4/160847)
- Apple Music Playlist Link (https://music.apple.com/jp/playlist/bocchi-the-rock/pl.u-Ympg5s39LRqp)

# Key config options

| Option | Default | Meaning |
|--------|---------|---------|
| `[instance] url` | `127.0.0.1:8080` | wrapper-lite HTTP instance |
| `[instance] secure` | `false` | https when `true` |
| `[download] streamDecrypt` | `true` | decrypt while downloading (边下边解) |
| `[download] decryptBatchSize` | `256` | Temari stream batch size |
| `[download] downloadTimeout` | `60` | idle timeout (s) for CDN streaming |
| `[download] resumeDownload` | `true` | resume interrupted downloads via Range |
| `[download] parallelNum` | `4` | concurrent song downloads (measured optimum 2-4) |
| `[download] appleCDNIP` | `""` | pin the CDN host to an IP (e.g. `17.253.85.201`); empty = system DNS |
| `[mv] maxHeight` | `1080` | maximum music-video resolution |

# Run

Requirements: Python 3.11+ and [uv](https://docs.astral.sh/uv/). A wrapper/lite instance (HTTP) is required — logging into Apple Music is done **on the wrapper side** (`lite --login user:pass`), not by this client. Optionally, `localInstance` (qemu) can run wrapper-lite locally (experimental, off by default).

For Android users: [android-deploy.md](/android-deploy.md)

For Windows users: use [the pre-configured version](https://nightly.link/WorldObservationLog/AppleMusicDecrypt/workflows/win-build/v3/AppleMusicDecrypt-Windows.zip) that works out of the box
```shell
git clone https://github.com/WorldObservationLog/AppleMusicDecrypt.git
cd AppleMusicDecrypt
git checkout v3
uv sync
cp config.example.toml config.toml
uv run python main.py
```

Point `[instance]` in `config.toml` at your wrapper-lite instance. The default is `127.0.0.1:8080` (http) for a locally self-hosted wrapper.

# FAQ
### Song did not pass the integrity check
The v3 integrity check is a structural parse of the output file. There are two possible causes for this problem:
1. Potential wrapper decryption error. This problem usually disappears after a few days. You can try restarting the wrapper, changing the wrapper instance, or waiting for a few days.
2. The audio source file provided by Apple Music is damaged. See more: https://t.me/abcthoughts/6294

### The bit depth of the ripped audio file does not match the selected codec
Some audio files provided by Apple Music are incorrectly encoded to a higher bit depth. This does not affect the content of the audio itself.
