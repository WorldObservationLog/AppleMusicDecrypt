# AppleMusicDecrypt - Telegram Bot

This is a Telegram Bot module for the **AppleMusicDecrypt** project. It allows you to download music directly from Apple Music to Telegram with ease.

## Features

- **Direct Download**: Send a supported Apple Music link (Song, Album, Playlist, Artist), and the bot will reply with the music files.
- **Multiple Audio Codecs**: Support checking (`/qa <url>`) and downloading (`/dl <url> -c <codec>`) audio in different formats (ALAC, AAC, Atmos, etc.), following the main project's capabilities.
- **Background Ripping & Caching**: Powered by AsyncIO. Features caching mechanisms (both SQLite and loose file_id matching) to skip downloading already ripped songs.
- **Queue System**: Personal and global concurrency limits protect the server and avoid Telegram rate limits.
- **Admin Commands**: Built-in support for `/whitelist`, `/blacklist`, and `/gstatus` to manage user access and view bot load.
- **User Settings**: Users can specify their own language preferences (`/settings`) to fetch metadata in specific regions.
- **2FA Login**: Fully supports logging into Telegram bots that require 2FA passwords.

## Setup & Run

### 1. Prerequisites

You must set up a Local Telegram Bot API Server if you plan to download and upload large files (especially for ALAC/Hi-Res files exceeding 50 MB).
- Follow the official guide: [Telegram Bot API - Local Server](https://core.telegram.org/bots/api#using-a-local-bot-api-server)

### 2. Configuration

Copy the example configuration file and fill in your details:
```shell
cp extras/telegram_bot/config.example.toml extras/telegram_bot/config.toml
```

Edit `extras/telegram_bot/config.toml` to configure:
- `api_id` and `api_hash`: Your Telegram API credentials.
- `bot_token`: Your Telegram Bot token from [@BotFather](https://t.me/BotFather).
- `local_server`: URL to your local Telegram API server (e.g., `http://127.0.0.1:8081`).
- `admin_ids`: A list of Telegram User IDs for bot administrators.
- Download limits, cache options, and more limits.

### 3. Running the Bot

Run the bot module from the root directory using Poetry:
```shell
poetry run python extras/telegram_bot/main.py
```

## Commands

### User Commands
- `/status` - Check the status of your current tasks.
- `/dl <Apple Music URL> [-f] [-c codec]` - Add an Apple Music URL to the download queue.
  - `-f`: Force download (bypass cache).
  - `-c <codec>`: Specify the audio codec.
- `/quality <Apple Music Song URL>` - Check available audio qualities and codecs for a song.
- `/settings` - Open the user settings panel and select preferred metadata language.
- `/gstatus` - View global server status and queue load.

### Admin Commands (Requires Admin ID)
- `/whitelist <add|remove|list> <user_id>` - Manage the bot's whitelist.
- `/blacklist <add|remove|list> <user_id>` - Manage the bot's blacklist.
- `/logout` - Log out the bot from the current session.
