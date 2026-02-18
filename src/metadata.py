from typing import Optional, Dict, List

from creart import it
from mutagen.mp4 import MP4Cover
from pydantic import BaseModel


from src.models import AlbumMeta
from src.models.song_data import Datum
from src.utils import ttml_convent, count_total_track_and_disc

NOT_INCLUDED_FIELD = ["playlistIndex", "bit_depth", "sample_rate", "sample_rate_kHz",
                      "track_total", "disk_total", "cover_url"]
TAG_MAPPING = {
    "song_id": "cnID",  # iTunes Catalog ID
    "title": "©nam",  # MP4 title
    "artist": "©ART",  # MP4 artist
    "album_id": "plID",  # iTunes Album ID
    "album_artist": "aART",  # MP4 album artist
    "album": "©alb",  # MP4 album
    "album_created": "©day",  # MP4 YEAR tag
    "composer": "©wrt",  # MP4 composer
    "genre": "©gen",  # MP4 genre
    "created": "purd",  # MP4 iTunes Purchase Date
    "track": "©trk",  # MP4 track name
    "tracknum": "trkn",  # MP4 total track number and current
    "disk": "disk",  # MP4 disc number
    "lyrics": "©lyr",  # MP4 unsynced lyrics
    "cover": "covr",  # MP4 cover art atom
    "copyright": "cprt",  # MP4 copyright
    "record_company": "©pub",  # MP4 publisher
    "upc": "----:com.apple.iTunes:BARCODE",  # MP4 barcode (UPC)
    "isrc": "----:com.apple.iTunes:ISRC",  # MP4 ISRC
    "rtng": "rtng",  # MP4 advisory rating
    "artist_id": "atID"  # iTunes Artist ID
}


class SongMetadata(BaseModel):
    song_id: Optional[str] = None
    title: Optional[str] = None
    artist: Optional[str] = None
    album_id: Optional[str] = None
    album_artist: Optional[str] = None
    album: Optional[str] = None
    album_created: Optional[str] = None
    composer: Optional[str] = None
    genre: Optional[List[str]] = None
    created: Optional[str] = None
    track: Optional[str] = None
    tracknum: Optional[int] = None
    track_total: Optional[Dict[int, int]] = None
    disk: Optional[int] = None
    disk_total: Optional[int] = None
    lyrics: Optional[str] = None
    cover: bytes = None
    cover_url: Optional[str] = None
    copyright: Optional[str] = None
    record_company: Optional[str] = None
    upc: Optional[str] = None
    isrc: Optional[str] = None
    rtng: Optional[int] = None
    playlist_index: Optional[int] = None
    bit_depth: Optional[int] = None
    sample_rate: Optional[int] = None
    sample_rate_kHz: Optional[str] = None
    artist_id: Optional[str] = None

    def to_itags_params(self, embed_metadata: list[str]):
        tags = []
        for key, value in self.model_dump().items():
            if not value:
                continue
            if key in embed_metadata and value:
                if key in NOT_INCLUDED_FIELD:
                    continue
                if key == "lyrics":
                    lrc = ttml_convent(value)
                    tags.append(f"{key}={lrc}")
                    continue
                if key.lower() in ('upc', 'isrc'):
                    # https://github.com/gpac/gpac/issues/3259
                    # tags.append(f"WM/{key.lower()}={value}")
                    continue
                if key == 'composer':
                    tags.append(f"writer={value}")
                    continue
                tags.append(f"{key}={value}")
        return ":".join(tags)

    def to_mutagen_tags(self, embed_metadata: list[str]):
        tags = {}
        for key, value in self.model_dump().items():
            if not value and key != "rtng":
                continue
            if key in embed_metadata:
                if key in NOT_INCLUDED_FIELD:
                    continue
                if key == "lyrics":
                    lrc = ttml_convent(value)
                    tags.update({TAG_MAPPING[key]: lrc})
                    continue
                if key == "tracknum":
                    tags.update({TAG_MAPPING[key]: ((value, self.track_total[self.disk]),)})
                    continue
                if key == "disk":
                    tags.update({TAG_MAPPING[key]: ((value, self.disk_total),)})
                    continue
                if key == "cover":
                    tags.update({TAG_MAPPING[key]: (MP4Cover(value),)})
                    continue
                if key == "upc":
                    tags.update({TAG_MAPPING[key]: (value.encode(),)})
                    continue
                if key == "isrc":
                    tags.update({TAG_MAPPING[key]: (value.encode(),)})
                    continue
                if key == "genre":
                    tags.update({TAG_MAPPING[key]: value})
                    continue
                if key == "rtng":
                    tags.update({TAG_MAPPING[key]: (value,)})
                    continue
                if key == "song_id":
                    tags.update({TAG_MAPPING[key]: (int(value),)})
                    continue
                if key == "album_id":
                    tags.update({TAG_MAPPING[key]: (int(value),)})
                    continue
                if key == "artist_id":
                    tags.update({TAG_MAPPING[key]: (int(value),)})
                    continue
                tags.update({TAG_MAPPING[key]: str(value)})
        return tags

    @classmethod
    def parse_from_song_data(cls, song_data: Datum):
        return cls(title=song_data.attributes.name, artist=song_data.attributes.artistName,
                   album_artist=song_data.relationships.albums.data[0].attributes.artistName,
                   album=song_data.attributes.albumName, composer=song_data.attributes.composerName,
                   genre=song_data.attributes.genreNames, created=song_data.attributes.releaseDate,
                   track=song_data.attributes.name, tracknum=song_data.attributes.trackNumber,
                   disk=song_data.attributes.discNumber, lyrics="", cover_url=song_data.attributes.artwork.url,
                   copyright=song_data.relationships.albums.data[0].attributes.copyright,
                   record_company=song_data.relationships.albums.data[0].attributes.recordLabel,
                   upc=song_data.relationships.albums.data[0].attributes.upc,
                   isrc=song_data.attributes.isrc,
                   album_created=song_data.relationships.albums.data[0].attributes.releaseDate,
                   rtng=cls._rating(song_data.attributes.contentRating),
                   song_id=song_data.id, album_id=song_data.relationships.albums.data[0].id,
                   artist_id=song_data.relationships.artists.data[0].id
                   )

    def parse_from_album_data(self, album_data: AlbumMeta):
        self.disk_total, self.track_total = count_total_track_and_disc(album_data.data[0].relationships.tracks)

    @staticmethod
    def _rating(content_rating: Optional[str]) -> int:
        if not content_rating:
            return 0
        if content_rating == "explicit":
            return 1
        if content_rating == "clean":
            return 2
        return None

    def set_lyrics(self, lyrics: str):
        self.lyrics = lyrics


    def set_playlist_index(self, index: int):
        self.playlist_index = index

    def set_bit_depth_and_sample_rate(self, bit_depth: int, sample_rate: int):
        self.bit_depth = bit_depth
        self.sample_rate = sample_rate
        self.sample_rate_kHz = str(sample_rate / 1000)
