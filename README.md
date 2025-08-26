# AppleMusicDecrypt

Apple Music decryption tool, based
on [zhaarey/apple-music-alac-atmos-downloader](https://github.com/zhaarey/apple-music-alac-atmos-downloader)

**WARNING: This project is currently in an extremely early stage, and there are still a large number of undiscovered
bugs and unfinished features. USE IT WITH CAUTION.**

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
```

# Support Codec

- `alac (audio-alac-stereo)`
- `ec3 (audio-atmos / audio-ec3)`
- `ac3 (audio-ac3)`
- `aac (audio-stereo)`
- `aac-binaural (audio-stereo-binaural)`
- `aac-downmix (audio-stereo-downmix)`
- `aac-legacy (audio-stereo, non-lossless audio)`

# Support Link

- Apple Music Song Share Link (https://music.apple.com/jp/album/%E5%90%8D%E3%82%82%E3%81%AA%E3%81%8D%E4%BD%95%E3%82%82%E3%81%8B%E3%82%82/1688539265?i=1688539274)
- Apple Music Album Share Link (https://music.apple.com/jp/album/nameless-name-single/1688539265)
- Apple Music Song Link (https://music.apple.com/jp/song/caribbean-blue/339592231)
- Apple Music Artist Link (https://music.apple.com/jp/artist/%E3%82%A8%E3%83%B3%E3%83%A4/160847)
- Apple Music Playlist Link (https://music.apple.com/jp/playlist/bocchi-the-rock/pl.u-Ympg5s39LRqp)

# About V2
AppleMusicDecrypt v2 provides support for remote fast decryption through [WorldObservationLog/wrapper](https://github.com/WorldObservationLog/wrapper) and [WorldObservationLog/wrapper-manager](https://github.com/WorldObservationLog/wrapper-manager)

By connecting to a public wrapper-manager instance on the Internet, ripping can be completed without an Apple account or an active Apple Music subscription.

For faster decryption, wrapper-manager can also be deployed locally. The decryption speed of a single wrapper instance can reach up to 40MB/s

A wrapper-manager instance for testing: 
```toml
[instance]
url = "wm.wol.moe"
secure = true
```

## Run
For Android users: [android-deploy.md](/android-deploy.md)

For Windows users: use [the pre-configured version](https://nightly.link/WorldObservationLog/AppleMusicDecrypt/workflows/win-build/v2/AppleMusicDecrypt-Windows.zip) that works out of the box
```shell
git clone https://github.com/WorldObservationLog/AppleMusicDecrypt.git
cd AppleMusicDecrypt
bash ./tools/install-deps.sh
poetry install
cp config.example.toml config.toml
poetry run python main.py
```
