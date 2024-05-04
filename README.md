# AppleMusicDecrypt

Apple Music decryption tool, based
on [zhaarey/apple-music-alac-atmos-downloader](https://github.com/zhaarey/apple-music-alac-atmos-downloader)

**WARNING: This project is currently in an extremely early stage, and there are still a large number of undiscovered
bugs and unfinished features. USE IT WITH CAUTION.**

# Usage

```shell
# Download song/album with default codec (alac)
download https://music.apple.com/jp/album/nameless-name-single/1688539265
# Download song/album with specified codec
download https://music.apple.com/jp/song/caribbean-blue/339592231 -c aac
# Download song from specified m3u8 with default codec (alac)
m3u8 https://aod.itunes.apple.com/itunes-assets/HLSMusic116/v4/cb/f0/91/cbf09175-ce98-d133-1936-2e46b6992aa5/P631756252_lossless.m3u8
# Start Mitm mode
mitm
```

# Support Codec

- `alac (audio-alac-stereo)`
- `ec3 (audio-atmos / audio-ec3)`
- `ac3 (audio-ac3)`
- `aac (audio-stereo)`
- `aac-binaural (audio-stereo-binaural)`
- `aac-downmix (audio-stereo-downmix)`

# Support Link

- Apple Music Song Share
  Link (https://music.apple.com/jp/album/%E5%90%8D%E3%82%82%E3%81%AA%E3%81%8D%E4%BD%95%E3%82%82%E3%81%8B%E3%82%82/1688539265?i=1688539274)
- Apple Music Album Share Link (https://music.apple.com/jp/album/nameless-name-single/1688539265)
- Apple Music Song Link (https://music.apple.com/jp/song/caribbean-blue/339592231)

# Deploy

## Prepare Local Environment

1. Install [GPAC](https://gpac.io/downloads/gpac-nightly-builds/)
2. Download [Bento4 MP4Tools](https://www.bento4.com/downloads/) and add the executable files to the environment
   variables
3. Run `gpac -version`, `mp4box -version`, `mp4extract`, `mp4edit` and make sure all the commands run fine

## Prepare Android Environment

### For WSA (Recommend):

1. Install Apple Music (3.6.0-beta) and login
2. Play a song in Apple Music
3. Install WSA from [LSPosed/MagiskOnWSALocal](https://github.com/LSPosed/MagiskOnWSALocal). Choose the version that
   includes Magisk but not GApps
4. Install following Magisk
   modules: [magisk-frida](https://github.com/ViRb3/magisk-frida), [sqlite3-magisk-module](https://github.com/rojenzaman/sqlite3-magisk-module)
5. Edit `config.toml`

```toml
[[devices]]
host = "127.0.0.1"
port = 58526 # Replace this value to your WSA ADB port!
agentPort = 10020
fridaPath = "/system/bin/frida-server"
suMethod = "su -c"
```

### For Google Android Emulator

1. Install Apple Music (3.6.0-beta) and login
2. Play a song in Apple Music
3. Manually install Frida and start frida-server in background
4. Edit `config.toml`

```toml
[[devices]]
host = "127.0.0.1"
port = 5555
agentPort = 10020
fridaPath = "/data/local/tmp/frida-server-16.2.1-android-x86_64"  # Replace this value to your frida-server path!
suMethod = "su 0"
```

## Run Script

### Use pre-built script (For Windows)

Download latest build from [Actions](https://github.com/WorldObservationLog/AppleMusicDecrypt/actions) (need login your
GitHub account). Unzip it, and run `main.exe`

### Manually Run

```shell
git clone https://github.com/WorldObservationLog/AppleMusicDecrypt.git
cd AppleMusicDecrypt
poetry install
poetry run python main.py
```

# Mitm Mode

## Prepare Environment

1. Install [Proxifier](https://www.proxifier.com/) and [Apple Music Windows](https://apps.microsoft.com/detail/9pfhdd62mxs1)
2. Sign in to Apple Music Windows with your Apple account
3. Run `poetry run mitmproxy` under the script directory and quit
4. Install the CA certificate according
   to [Mitmproxy's instructions](https://docs.mitmproxy.org/stable/concepts-certificates/#quick-setup)
5. Create a new proxy server in Proxifier(`Profile -> Proxy Servers -> Add`). `Address` and `Port` are values
   of `mitm.host` and `mitm.port` in `config.toml`, and `Protocol` is `SOCKS Version 5`
6. Create a new proxification rule in Proxifier(`Profile -> Proxy Servers -> Add`). Fill in `Applications` field
   with `amplibraryagent.exe; applemusic.exe` and select the proxy server just created in `Action`

## Start Mitm

1. Run the script, type command `mitm`
2. Start Proxifier
3. Start Apple Music Windows and play a song or an album.
4. Now the script should catch the m3u8 link and start ripping