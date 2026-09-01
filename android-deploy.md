# Deploy AppleMusicDecrypt (v3) on Android
This deployment requires that you have a wrapper-lite HTTP instance available
(local, or a remote one you trust). Login is done on the wrapper side.
## Step 1: Install Termux and Debian
Download and install [Termux](https://termux.dev/). Give it storage permissions(`termux-setup-storage`)

Then execute the following commands to install Debian:
```shell
pkg update && pkg install proot-distro
pd i debian
```
## Step 2: Deploy AppleMusicDecrypt
Enter the Debian environment(`pd login debian`)
```shell
apt update && apt install git -y && curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.bashrc
git clone https://github.com/WorldObservationLog/AppleMusicDecrypt
cd AppleMusicDecrypt
git checkout v3
uv sync
cp config.example.toml config.toml
nano config.toml
```

If `uv sync` fails with "built wheel ... is not compatible with the current
Python ... on Android aarch64", your uv binary is the Android build and it
rejects Linux wheels. Fall back to pip + venv:
```shell
apt install python3-venv python3-pip -y
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```
## Step3: Edit config
For Android users, some configurations need to be modified.
```toml
[instance]
url = "127.0.0.1:8080" # Address of your wrapper-lite instance
secure = false

[download]
parallelNum = 2 # The recommended value is half of maxRunningTasks
maxRunningTasks = 4 # This value depends on the memory size of the device and is not recommended to be higher than 8
dirPathFormat = "/sdcard/Music/{album_artist}/{album}"
playlistDirPathFormat = "/sdcard/Music/playlists/{playlistName}"
```
## Step 4: Run AppleMusicDecrypt

uv install:
`uv run python main.py`

pip fallback install:
`.venv/bin/python main.py`

If startup fails with `no libtemari found for this platform`, the error
includes the detected platform key (e.g. `[platform key: linux-aarch64, ...]`).
Inside proot Debian it should be `linux-aarch64`; if it differs, set
`TEMARI_LIB` to the matching library inside
`site-packages/temari/lib/<key>/libtemari.so`.
## Update AppleMusicDecrypt
```shell
pd login debian
cd AppleMusicDecrypt
git checkout -f && git pull
uv sync               # or: .venv/bin/pip install -r requirements.txt
```
