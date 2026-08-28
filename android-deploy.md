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
apt update && apt install pipx git -y && pipx install poetry && pipx ensurepath && source ~/.bashrc
git clone https://github.com/WorldObservationLog/AppleMusicDecrypt
cd AppleMusicDecrypt
git checkout v3
poetry env use /usr/bin/python3 && poetry install
cp config.example.toml config.toml
nano config.toml
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
`poetry run python main.py`
## Update AppleMusicDecrypt
```shell
pd login debian
cd AppleMusicDecrypt
git checkout -f && git pull
poetry update
cp config.example.toml config.toml
nano config.toml
```