import tomllib

from pydantic import BaseModel


class Language(BaseModel):
    language: str
    languageForGenre: str


class Device(BaseModel):
    host: str
    port: int
    agentPort: int
    fridaPath: str
    suMethod: str


class Download(BaseModel):
    atmosConventToM4a: bool
    songNameFormat: str
    dirPathFormat: str
    saveLyrics: bool
    saveCover: bool
    coverFormat: str
    afterDownloaded: str


class Metadata(BaseModel):
    embedMetadata: list[str]


class Config(BaseModel):
    language: Language
    devices: list[Device]
    download: Download
    metadata: Metadata

    @classmethod
    def load_from_config(cls, config_file: str = "config.toml"):
        with open(config_file, "r") as f:
            config = tomllib.loads(f.read())
        return cls.parse_obj(config)
