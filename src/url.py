from urllib.parse import urlparse, parse_qs

from pydantic import BaseModel


class URLType:
    Song = "song"
    Album = "album"
    Playlist = "playlist"
    Artist = "artist"


class AppleMusicURL(BaseModel):
    url: str
    storefront: str
    type: str
    id: str

    @classmethod
    def parse_url(cls, url: str):
        parsed_url = urlparse(url)
        paths = parsed_url.path.split("/")
        storefront = paths[1]
        url_type = paths[2]
        match url_type:
            case URLType.Song:
                url_id = paths[4]
                return Song(url=url, storefront=storefront, id=url_id, type=URLType.Song)
            case URLType.Album:
                if not parsed_url.query:
                    url_id = paths[4]
                    return Album(url=url, storefront=storefront, id=url_id, type=URLType.Album)
                else:
                    url_query = parse_qs(parsed_url.query)
                    if url_query.get("i"):
                        url_id = url_query.get("i")[0]
                        return Song(url=url, storefront=storefront, id=url_id, type=URLType.Song)
                    else:
                        url_id = paths[4]
                        return Album(url=url, storefront=storefront, id=url_id, type=URLType.Album)
            case URLType.Artist:
                url_id = paths[4]
                return Artist(url=url, storefront=storefront, id=url_id, type=URLType.Artist)
            case URLType.Playlist:
                url_id = paths[4]
                return Playlist(url=url, storefront=storefront, id=url_id, type=URLType.Playlist)


class Song(AppleMusicURL):
    ...


class Album(AppleMusicURL):
    ...


class Playlist(AppleMusicURL):
    ...


class Artist(AppleMusicURL):
    ...
