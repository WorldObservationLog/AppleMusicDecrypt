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
- **Full-screen TUI** — a live log pane, a tree-structured task sidebar (album → tracks), a command input with history/completion, a floating batch-URL panel and a status bar. Built on [Textual](https://textual.textualize.io/). A `--legacy-ui` flag switches to a plain-terminal REPL for minimal terminals.

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

The whole interface is a full-screen terminal application with three areas: a live log pane, a task sidebar showing downloads as a tree, and a command input bar with a status bar underneath. `login` and `logout` ask for their input in a small modal dialog, so the terminal is never handed to a second program.

## Command input
* `Enter` runs the command. `↑`/`↓` walk history; the current token is completed inline — accept the suggestion with `→`. A second line under the field lists every candidate for the token you are typing, so `dl --` shows all the flags at once.
* Batch mode (`dl -b`) opens a modal panel — type one URL per line, `Ctrl+D` submits, `Esc` cancels. It counts what you pasted as you go (lines, unique URLs, duplicates, and anything that does not look like a URL), wraps long URLs instead of scrolling sideways, and collapses duplicates on submit.
* History and your UI preferences (theme, sidebar width) are kept between sessions, in `.applemusicdecrypt_history` and `.applemusicdecrypt_tui` next to `config.toml`.

## Keys
These apply to the full-screen UI; `--legacy-ui` has its own (much smaller) set, listed under [Minimal terminals](#minimal-terminals---legacy-ui). `F1` opens the same list as a scrollable panel.

| Key | Action |
|-----|--------|
| `Tab` / `Shift+Tab` | complete the word being typed, or move focus when there is nothing to complete |
| `Ctrl+Left / Ctrl+Right`, `Alt+Left / Alt+Right` | resize the task sidebar (the Alt pair works while the command line has focus) |
| `↑ / ↓ / PgUp / PgDn` | scroll the log / move through the task list, whichever has focus |
| `Enter` | fold or unfold the selected group in the task sidebar |
| `End` | re-enable log auto-follow after scrolling |
| `F1` | help panel (keys, commands, examples) |
| `F2` | cycle the log level floor: everything → `WARNING`+ → `ERROR`+ |
| `F3` (or `/`) | filter the log by substring; `Esc` clears it |
| `F4` | cycle the colour theme (dark and light) |
| `F5` / `F6` | expand / fold every group in the sidebar |
| `F7` | copy the highlighted task's name |
| `F8` | retry the highlighted task — a group retries only its failures |
| `F9` | cancel the task under the cursor |
| `Delete` | remove the highlighted task, if it has finished |
| `Ctrl+S` | write the log buffer to `applemusicdecrypt-<timestamp>.log` |
| `Ctrl+V` | paste from the system clipboard (a multi-line paste is folded to one line) |
| `Ctrl+P` | command palette (search every action by name) |
| `F10` / `Ctrl+C` | exit (press twice when tasks are running) |
| `Ctrl+D` | submit the batch panel (batch mode) |
| `Esc` | cancel the batch panel / the login prompt / the log filter / a table |

Mouse is supported: click a pane to focus it, click a task to fold it, wheel to scroll.

A failed track is not a dead end: `F8` runs it again from the request that created it, going back through the same download path as the original command. A group retries only the tracks that failed. `F9` cancels a running track without touching its siblings — an album's tracks are independent coroutines.

A download is something you start and walk away from, so finishing is announced rather than implied: a notification when the last track settles, including how many failed.

On a terminal narrower than 52 columns the sidebar is dropped entirely and the log takes the whole width — a 20-column sidebar beside a 24-column log is unusable, and the log is the pane that still says what is happening.

## Quality inspection
`qa <url>` opens the result as a scrollable table rather than printing it into the log. A `tabulate` grid is a fixed ~90 columns wide no matter the terminal, so in a 68-column log pane every border wrapped onto a second line and the columns stopped lining up. `qa <album>` asks the wrapper once per track, so the results are collected briefly and merged into one table with a `Song` column; `c` copies the highlighted row as TSV. `qa` output still prints as a grid under `--legacy-ui` and on a plain terminal.

## Fonts and colour
Every icon in the sidebar is an emoji, and a terminal without a font that covers them shows boxes or single-width mojibake — which misaligns the columns, since their width is measured rather than assumed. Set `APPLEMUSICDECRYPT_ASCII=1` to swap the whole set for plain ASCII (`+` done, `x` failed, `s` song, …); the measured widths stay correct either way. `F4` cycles both dark and light themes: the chrome follows the theme's own variables and the task rows, status bar and help text switch palette with it.

Failures are surfaced twice: as a line in the log, and as a notification in the top-right corner, so an error that scrolls past is not missed. Toasts are rate-limited — a batch download that loses the network fails every track at once, and one toast per failure used to cover the whole interface. Only a few appear per burst; the rest are counted in the status bar as `[+5 more errors]`.

Both panes adapt to the terminal instead of scrolling sideways: text wraps to the width each one has. The sidebar starts at about a third of the terminal and gives up columns first when the terminal gets narrow, so the log pane always keeps a workable width. `Ctrl+Left`/`Ctrl+Right` pin it to a width of your own.

The status bar is laid out against the terminal width rather than clipped: the speed sparkline, track progress and ETA drop off first as the terminal narrows, and the key hints stay pinned to the right edge. It also reports the active log filter, any merged error count, and — while the log is scrolled back — how many lines have piled up below (`↓12`).

Log lines are trimmed for the pane: the wall-clock timestamp keeps only `HH:MM:SS`, and `RipLogger`'s `| SONG | artist - title |` prefix becomes just the title. Together that is 32 columns saved per line, which is the difference between one row and two on a narrow pane.

## Quality inspection
`qa <url>` opens the result as a scrollable table rather than printing it into the log. A `tabulate` grid is a fixed ~90 columns wide no matter the terminal, so in a 68-column log pane every border wrapped onto a second line and the columns stopped lining up. `qa <album>` asks the wrapper once per track, so the results are collected briefly and merged into one table with a `Song` column. `qa` output still prints as a grid under `--legacy-ui` and on a plain terminal.

## Task sidebar
Tasks are shown as a tree: an album / playlist / artist download creates a parent node; each song appears underneath with a live status icon (⏳ waiting, 🔄 running, ✅ done, ✔ already existed, ❌ failed, ⚠ some failed) and its download progress. Music videos appear as 🎬 nodes.

A group that can be folded carries a `▼`/`▶` marker and folds on `Enter` or a click; songs are leaves and carry none. A long `artist - title` wraps onto as many lines as it needs rather than being cut off or pushing the list sideways.

Running rows show `44%` for a track and `2/4` for a group. A group folds itself once every track is done, but opening it by hand keeps it open. A finished group that had failures says so — `2/4 ⚠2` — because `4/4` reads the same whether everything worked or half the album broke. Until the first task arrives the sidebar shows the three commands you are most likely to want.

## Minimal terminals (`--legacy-ui`)
When the full-screen interface cannot run — no pty, an unusual `TERM`, a serial console — start with `--legacy-ui`: a plain `>` prompt with no alternate screen. It keeps the same commands and history/line editing (supplied by the terminal itself), prints the speed/task line above each prompt, and lists valid words on demand: end a command with `?`, e.g. `dl --codec ?`. `Ctrl+C` or `Ctrl+D` quits; both go through the same two-step confirmation as `F10` while tasks are running. If the full-screen TUI fails to start, the error message points at this flag.

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
| `[instance] url` | `127.0.0.1:12340` | wrapper-lite HTTP instance |
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

Point `[instance]` in `config.toml` at your wrapper-lite instance. The default is `127.0.0.1:12340` (http) for a locally self-hosted wrapper.

# FAQ
### Song did not pass the integrity check
The v3 integrity check is a structural parse of the output file. There are two possible causes for this problem:
1. Potential wrapper decryption error. This problem usually disappears after a few days. You can try restarting the wrapper, changing the wrapper instance, or waiting for a few days.
2. The audio source file provided by Apple Music is damaged. See more: https://t.me/abcthoughts/6294

### The bit depth of the ripped audio file does not match the selected codec
Some audio files provided by Apple Music are incorrectly encoded to a higher bit depth. This does not affect the content of the audio itself.
