import tomllib
from typing import Type

from creart import exists_module
from creart.creator import AbstractCreator, CreateTargetInfo
from pydantic import BaseModel

CONFIG_VERSION = "0.1.0"


class Instance(BaseModel):
    url: str = "127.0.0.1:8080"
    secure: bool = False


class LocalInstance(BaseModel):
    enable: bool = False
    enableHardwareAcceleration: bool = False
    hardwareAccelerator: str = ""
    memorySize: str = "512M"
    cpuModel: str = "Cascadelake-Server-v5"
    showWindow: bool = False
    startArgs: str = "-host 0.0.0.0 -port 8080 -log-level info"
    # Optional: override where the wrapper-lite qemu image zip is downloaded from.
    imageUrl: str = ""


class Region(BaseModel):
    language: str = "zh-Hant-HK"
    languageNotExistWarning: bool = True


class Download(BaseModel):
    proxy: str = ""
    parallelNum: int = 1
    maxRunningTasks: int = 128
    appleCDNIP: str = ""
    # Decrypt samples while the media file is still being downloaded.
    streamDecrypt: bool = True
    # Batch size used by Temari's streaming decryptor.
    decryptBatchSize: int = 256
    # Idle timeout (seconds) for the streaming CDN download; 0 disables.
    downloadTimeout: int = 60
    # Resume an interrupted download from the last complete box boundary.
    resumeDownload: bool = True
    codecAlternative: bool = True
    codecPriority: list[str] = ["alac", "ec3", "ac3", "aac"]
    atmosConventToM4a: bool = True
    failedSongNotPassIntegrityCheck: bool = False
    audioInfoFormat: str = ""
    songNameFormat: str = "{disk}-{tracknum:02d} {title}"
    dirPathFormat: str = "downloads/{album_artist}/{album}"
    playlistDirPathFormat: str = "downloads/playlists/{playlistName}"
    playlistSongNameFormat: str = "{playlistSongIndex:02d}. {artist} - {title}"
    saveLyrics: bool = True
    lyricsFormat: str = "lrc"
    # Word-timed (karaoke) lyrics: wrapper /lyrics syllable=1 + per-word LRC tags.
    lyricsSyllable: bool = False
    lyricsExtra: list[str] = ["translation", "pronunciation"]
    # Low-memory mode: all large files (MV segments, batch audio downloads) are
    # spilled to disk and the MV muxer streams from disk instead of RAM.
    lowMemory: bool = False
    # fsync each finished file (durability vs throughput); disable for
    # large batches to avoid blocking the event loop on synchronous fsync.
    fsync: bool = True
    saveCover: bool = True
    coverFormat: str = "jpg"
    coverSize: str = "5000x5000"
    maxSampleRate: int = 192000
    maxBitDepth: int = 24
    afterDownloaded: str = ""
    retryTime: int = 8
    maxWaitTime: int = 30


class Metadata(BaseModel):
    embedMetadata: list[str] = ["title", "artist", "album", "album_artist", "composer", "album_created",
                                "genre", "created", "track", "tracknum", "disk", "lyrics", "cover", "copyright",
                                "record_company", "upc", "isrc", "rtng", "song_id", "album_id", "artist_id"]


class MV(BaseModel):
    saveDir: str = "downloads/music-videos"
    # Maximum video height to download (0 = best available).
    maxHeight: int = 1080
    # MV audio rendition: atmos | ac3 | aac
    audioType: str = "atmos"
    # Concurrent segment downloads per stream (D2).
    segmentConcurrency: int = 4
    # Segment download retries (D2).
    segmentRetries: int = 3


class Config(BaseModel):
    version: str = "0.0.0"
    region: Region
    instance: Instance
    localInstance: LocalInstance
    download: Download
    metadata: Metadata
    mv: MV = MV()

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