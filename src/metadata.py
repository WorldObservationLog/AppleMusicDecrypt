from typing import Optional

from pydantic import BaseModel

from src.api import get_cover
from src.models.song_data import Datum
from src.utils import ttml_convent_to_lrc


class SongMetadata(BaseModel):
    title: Optional[str] = None
    artist: Optional[str] = None
    album_artist: Optional[str] = None
    album: Optional[str] = None
    composer: Optional[str] = None
    genre: Optional[str] = None
    created: Optional[str] = None
    track: Optional[str] = None
    tracknum: Optional[int] = None
    disk: Optional[int] = None
    lyrics: Optional[str] = None
    cover: bytes = None
    cover_url: Optional[str] = None
    copyright: Optional[str] = None
    record_company: Optional[str] = None
    upc: Optional[str] = None
    isrc: Optional[str] = None
    playlistIndex: Optional[int] = None

    def to_itags_params(self, embed_metadata: list[str]):
        tags = []
        for key, value in self.model_dump().items():
            if not value:
                continue
            if key in embed_metadata and value:
                if "playlist" in key:
                    continue
                if key == "cover":
                    continue
                if key == "lyrics":
                    lrc = ttml_convent_to_lrc(value)
                    tags.append(f"{key}={lrc}")
                    continue
                tags.append(f"{key}={value}")
        return ":".join(tags)

    @classmethod
    def parse_from_song_data(cls, song_data: Datum):
        return cls(title=song_data.attributes.name, artist=song_data.attributes.artistName,
                   album_artist=song_data.relationships.albums.data[0].attributes.artistName,
                   album=song_data.attributes.albumName, composer=song_data.attributes.composerName,
                   genre=song_data.attributes.genreNames[0], created=song_data.attributes.releaseDate,
                   track=song_data.attributes.name, tracknum=song_data.attributes.trackNumber,
                   disk=song_data.attributes.discNumber, lyrics="", cover_url=song_data.attributes.artwork.url,
                   copyright=song_data.relationships.albums.data[0].attributes.copyright,
                   record_company=song_data.relationships.albums.data[0].attributes.recordLabel,
                   upc=song_data.relationships.albums.data[0].attributes.upc,
                   isrc=song_data.attributes.isrc
                   )

    def set_lyrics(self, lyrics: str):
        self.lyrics = lyrics

    async def get_cover(self, cover_format: str, cover_size: str):
        self.cover = await get_cover(self.cover_url, cover_format, cover_size)

    def set_playlist_index(self, index: int):
        self.playlistIndex = index
