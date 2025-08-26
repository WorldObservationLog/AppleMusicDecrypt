import tomllib
from typing import Type

from creart import exists_module
from creart.creator import AbstractCreator, CreateTargetInfo
from pydantic import BaseModel

CONFIG_VERSION = "0.0.1"


class Instance(BaseModel):
    url: str = "127.0.0.1:8080"
    secure: bool = False


class Region(BaseModel):
    language: str = "zh-Hant-HK"
    languageNotExistWarning: bool = True


class Download(BaseModel):
    proxy: str = ""
    parallelNum: int = 1
    maxRunningTasks: int = 128
    appleCDNIP: str = ""
    codecAlternative: bool = True
    codecPriority: list[str] = ["alac", "ec3", "ac3", "aac"]
    atmosConventToM4a: bool = True
    audioInfoFormat: str = ""
    songNameFormat: str = "{disk}-{tracknum:02d} {title}"
    dirPathFormat: str = "downloads/{album_artist}/{album}"
    playlistDirPathFormat: str = "downloads/playlists/{playlistName}"
    playlistSongNameFormat: str = "{playlistSongIndex:02d}. {artist} - {title}"
    saveLyrics: bool = True
    saveCover: bool = True
    coverFormat: str = "jpg"
    coverSize: str = "5000x5000"
    afterDownloaded: str = ""


class Metadata(BaseModel):
    embedMetadata: list[str] = ["title", "artist", "album", "album_artist", "composer", "album_created",
                                "genre", "created", "track", "tracknum", "disk", "lyrics", "cover", "copyright",
                                "record_company", "upc", "isrc", "rtng"]


class Config(BaseModel):
    version: str = "0.0.0"
    region: Region
    instance: Instance
    download: Download
    metadata: Metadata

    @classmethod
    def load_from_config(cls, config_file: str = "config.toml"):
        with open(config_file, "r", encoding="utf-8") as f:
            config = tomllib.loads(f.read())
        return cls.model_validate(config)


class ConfigCreator(AbstractCreator):
    targets = (
        CreateTargetInfo("src.config", "Config"),
    )

    @staticmethod
    def available() -> bool:
        return exists_module("src.config")

    @staticmethod
    def create(create_type: Type[Config]) -> Config:
        return create_type.load_from_config()
