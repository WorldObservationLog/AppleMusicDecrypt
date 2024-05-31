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
    hyperDecrypt: bool
    hyperDecryptNum: int


class M3U8Api(BaseModel):
    enable: bool
    force: bool
    endpoint: str


class Download(BaseModel):
    proxy: str
    parallelNum: int
    getM3u8FromDevice: bool
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
    alacMax: int
    atmosMax: int
    afterDownloaded: str


class Metadata(BaseModel):
    embedMetadata: list[str]


class Config(BaseModel):
    region: Region
    devices: list[Device]
    m3u8Api: M3U8Api
    download: Download
    metadata: Metadata

    @classmethod
    def load_from_config(cls, config_file: str = "config.toml"):
        with open(config_file, "r", encoding="utf-8") as f:
            config = tomllib.loads(f.read())
        return cls.parse_obj(config)
