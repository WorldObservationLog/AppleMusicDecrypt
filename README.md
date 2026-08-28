# AppleMusicDecrypt

Apple Music decryption tool, inspired by [zhaarey/apple-music-alac-atmos-downloader](https://github.com/zhaarey/apple-music-alac-atmos-downloader)

Discussion Group: https://t.me/apple_music_alac

# v3 (this branch)

The v3 rewrite is built around the modern [WorldObservationLog/wrapper](https://github.com/WorldObservationLog/wrapper)
`lite` HTTP API and the [WorldObservationLog/Temari](https://github.com/WorldObservationLog/Temari)
local decryption library:

- **HTTP instead of gRPC** — the client talks to a wrapper/lite instance over
  plain JSON HTTP (`/m3u8 /key /lyrics /webplayback /license /status`).
- **Local streaming decryption (边下边解, default)** — the decrypt template is
  fetched once per song from `/key` and every sample is decrypted **locally**
  by the bundled `temari` Rust cdylib while the media file is still
  downloading. No per-sample round-trips to the wrapper, so throughput scales
  with your CPU cores.
- **Zero external binaries** — gpac / MP4Box / Bento4 / ffmpeg are gone. The
  fMP4 file is parsed, decrypted and re-encapsulated by a pure-Python ISO-BMFF
  module; tags are written with `mutagen`; integrity is verified structurally.
- **Batch-friendly** — prefetch key template reuse, cached metadata, streaming
  download with byte-range resume, per-fragment pipelined decryption.
- **Music videos** — `dl <music-video-url>` downloads the MV: video + audio
  streams are fetched, decrypted with Widevine (pure-Python AES-CBC cbcs,
  verified byte-for-byte against Bento4 mp4decrypt) and remuxed into one MP4
  by the pure-Python muxer — no MP4Box/ffmpeg.
- The interactive REPL (download / quality / status / batch mode) is kept and
  improved.

## Requirements

- Python 3.11+ and Poetry
- A wrapper/lite instance (HTTP). Logging into Apple Music is done **on the
  wrapper side** (`lite --login user:pass`), not by this client.
- Optional: `localInstance` (qemu) to run wrapper-lite locally — experimental,
  off by default.

## Usage

```shell
git clone https://github.com/WorldObservationLog/AppleMusicDecrypt.git
cd AppleMusicDecrypt
git checkout v3
poetry install
cp config.example.toml config.toml
poetry run python main.py
```

Point `[instance]` in `config.toml` at your wrapper-lite instance. The default
is `127.0.0.1:8080` (http) for a locally self-hosted wrapper.

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
# Batch mode: multiple URLs with the same options, without retyping the command
dl -c aac -l en-US -b
https://music.apple.com/jp/playlist/bocchi-the-rock/pl.u-Ympg5s39LRqp
https://music.apple.com/jp/album/nameless-name-single/1688539265
# Download a music video
dl https://music.apple.com/jp/music-video/1800449196
# Check the available quality of the song
quality https://music.apple.com/jp/playlist/bocchi-the-rock/pl.u-Ympg5s39LRqp
# Or a shorter command
qa https://music.apple.com/jp/playlist/bocchi-the-rock/pl.u-Ympg5s39LRqp
# You can hide a column by enabling it in the options.
qa --codec-id https://music.apple.com/jp/playlist/bocchi-the-rock/pl.u-Ympg5s39LRqp
# When you add --invert, it works the opposite way.
qa --invert --codec-id https://music.apple.com/jp/playlist/bocchi-the-rock/pl.u-Ympg5s39LRqp
```

## Support Codec

- `alac (audio-alac-stereo)`
- `ec3 (audio-atmos / audio-ec3)`
- `ac3 (audio-ac3)`
- `aac (audio-stereo)`
- `aac-binaural (audio-stereo-binaural)`
- `aac-downmix (audio-stereo-downmix)`
- `aac-legacy (audio-stereo, non-lossless audio, Widevine — pure-Python decrypt)`

## Support Link

- Apple Music Song Share Link
- Apple Music Album Share Link
- Apple Music Song Link
- Apple Music Artist Link
- Apple Music Playlist Link

## Key config options

| Option | Default | Meaning |
|--------|---------|---------|
| `[instance] url` | `127.0.0.1:8080` | wrapper-lite HTTP instance |
| `[instance] secure` | `false` | https when `true` |
| `[download] streamDecrypt` | `true` | decrypt while downloading (边下边解) |
| `[download] decryptBatchSize` | `256` | Temari stream batch size |
| `[download] downloadTimeout` | `60` | idle timeout (s) for CDN streaming |
| `[download] resumeDownload` | `true` | resume interrupted downloads via Range |

## FAQ

### Song did not pass the integrity check
The v3 integrity check is a structural parse of the output file. Failure
usually means a wrapper decryption error or a damaged Apple source file.

### The bit depth of the ripped audio file does not match the selected codec
Some audio files provided by Apple Music are incorrectly encoded to a higher
bit depth. This does not affect the content of the audio itself.