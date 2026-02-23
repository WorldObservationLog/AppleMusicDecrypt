import os
import tomllib
from typing import List

from pydantic import BaseModel

CURRENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(CURRENT_DIR, "config.toml")


class BotSettings(BaseModel):
    token: str = ""
    base_url: str = ""


class SystemSettings(BaseModel):
    keep_file: bool = False
    loose_cache: bool = True
    whitelist_mode: bool = False
    admin_ids: List[int] = []

class LimitsSettings(BaseModel):
    max_tasks_per_user: int = 10
    max_tasks_global: int = 50
    allowed_types: List[str] = ["song", "album", "artist", "playlist"]
    max_tracks: int = 100
    max_song_duration_sec: int = 1200
    max_total_duration_sec: int = 18000


class UserDefaultSettings(BaseModel):
    language: str = "follow-user"
    default_codec: str = "alac"


class TelegramBotConfig(BaseModel):
    bot: BotSettings
    system: SystemSettings
    limits: LimitsSettings
    user_default: UserDefaultSettings

    @classmethod
    def load_from_config(cls, config_file: str = CONFIG_PATH):
        try:
            with open(config_file, "rb") as f:
                config_data = tomllib.load(f)
            return cls.model_validate(config_data)
        except Exception as e:
            raise RuntimeError(f"Failed to load telegram bot config from {config_file}: {e}")


bot_config = TelegramBotConfig.load_from_config()
