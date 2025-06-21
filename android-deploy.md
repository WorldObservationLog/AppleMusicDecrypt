# Deploy AppleMusicDecrypt on Android
This deployment requires that you have an instance of wrapper-manager available.
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
apt update && apt install python3 python3-poetry
git clone https://github.com/WorldObservationLog/AppleMusicDecrypt && cd AppleMusicDecrypt
./tools/install-deps.sh
poetry install
cp config.example.toml config.toml
nano config.toml
```
## Step3: Edit config
For Android users, some configurations need to be modified.
```toml
[instance]
url = "wm.wol.moe" # Or use another wrapper-manager instance
secure = true

[download]
parallelNum = 2 # The recommended value is half of maxRunningTasks
maxRunningTasks = 4 # This value depends on the memory size of the device and is not recommended to be higher than 8
dirPathFormat = "/scdard/Music/{album_artist}/{album}"
playlistDirPathFormat = "/scdard/Music/playlists/{playlistName}"
```
## Step 4: Run AppleMusicDecrypt
`poetry run python main.py`