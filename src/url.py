from typing import Optional
from urllib.parse import urlparse, parse_qs

import regex
from pydantic import BaseModel


class URLType:
    Song = "song"
    Album = "album"
    Playlist = "playlist"
    Artist = "artist"
    MusicVideo = "music-video"


class AppleMusicURL(BaseModel):
    url: str
    storefront: str
    type: str
    id: str

    @classmethod
    def parse_url(cls, url: str):
        if not regex.match(r"https://music.apple.com/(.{2})/(song|album|playlist|artist|music-video).*/(pl.*|\d*)", url):
            return None
        parsed_url = urlparse(url)
        paths = parsed_url.path.split("/")
        storefront = paths[1]
        url_type = paths[2]
        match url_type:
            case URLType.Song:
                url_id = paths[-1]
                return Song(url=url, storefront=storefront, id=url_id, type=URLType.Song)
            case URLType.Album:
                if not parsed_url.query:
                    url_id = paths[-1]
                    return Album(url=url, storefront=storefront, id=url_id, type=URLType.Album)
                else:
                    url_query = parse_qs(parsed_url.query)
                    if url_query.get("i"):
                        url_id = url_query.get("i")[0]
                        return Song(url=url, storefront=storefront, id=url_id, type=URLType.Song)
                    else:
                        url_id = paths[-1]
                        return Album(url=url, storefront=storefront, id=url_id, type=URLType.Album)
            case URLType.Artist:
                url_id = paths[-1]
                return Artist(url=url, storefront=storefront, id=url_id, type=URLType.Artist)
            case URLType.Playlist:
                url_id = paths[-1]
                return Playlist(url=url, storefront=storefront, id=url_id, type=URLType.Playlist)
            case URLType.MusicVideo:
                url_id = paths[-1]
                return MusicVideo(url=url, storefront=storefront, id=url_id, type=URLType.MusicVideo)
        return None


class Song(AppleMusicURL):
    ...


class Album(AppleMusicURL):
    ...


class Playlist(AppleMusicURL):
    ...


class Artist(AppleMusicURL):
    ...


class MusicVideo(AppleMusicURL):
    ...


class TrackType:
    """``type`` of an item in an album/playlist ``tracks`` relationship."""
    Song = "songs"
    MusicVideo = "music-videos"


# An album/playlist track is either a song or a music video.
Track = Song | MusicVideo


def parse_track(track_id: str, track_type: str, storefront: str) -> Optional[Track]:
    """Build the URL object for an album/playlist track, or None for an unknown type."""
    match track_type:
        case TrackType.Song:
            return Song(id=track_id, storefront=storefront, url="", type=URLType.Song)
        case TrackType.MusicVideo:
            return MusicVideo(id=track_id, storefront=storefront, url="", type=URLType.MusicVideo)
    return None
