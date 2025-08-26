import tomllib
from typing import Type

from creart import exists_module
from creart.creator import AbstractCreator, CreateTargetInfo
from pydantic import BaseModel


class Instance(BaseModel):
    url: str
    secure: bool


class Region(BaseModel):
    language: str
    languageNotExistWarning: bool


class Download(BaseModel):
    proxy: str
    parallelNum: int
    maxRunningTasks: int
    appleCDNIP: str
    codecAlternative: bool
    codecPriority: list[str]
    atmosConventToM4a: bool
    audioInfoFormat: str
    songNameFormat: str
    dirPathFormat: str
    playlistDirPathFormat: str
    playlistSongNameFormat: str
    saveLyrics: bool
    saveCover: bool
    coverFormat: str
    coverSize: str
    afterDownloaded: str


class Metadata(BaseModel):
    embedMetadata: list[str]


class Config(BaseModel):
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
