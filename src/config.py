import tomllib

from pydantic import BaseModel


class Region(BaseModel):
    language: str
    defaultStorefront: str


class Device(BaseModel):
    host: str
    port: int
    agentPort: int
    suMethod: str


class M3U8Api(BaseModel):
    enable: bool
    endpoint: str


class Download(BaseModel):
    proxy: str
    parallelNum: int
    codecAlternative: bool
    codecPriority: list[str]
    atmosConventToM4a: bool
    songNameFormat: str
    dirPathFormat: str
    saveLyrics: bool
    saveCover: bool
    coverFormat: str
    coverSize: str
    afterDownloaded: str


class Metadata(BaseModel):
    embedMetadata: list[str]


class Mitm(BaseModel):
    host: str
    port: int


class Config(BaseModel):
    region: Region
    devices: list[Device]
    m3u8Api: M3U8Api
    download: Download
    metadata: Metadata
    mitm: Mitm

    @classmethod
    def load_from_config(cls, config_file: str = "config.toml"):
        with open(config_file, "r") as f:
            config = tomllib.loads(f.read())
        return cls.parse_obj(config)
