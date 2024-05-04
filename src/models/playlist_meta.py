from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class Description(BaseModel):
    standard: str
    short: str


class Artwork(BaseModel):
    width: int
    url: str
    height: int
    textColor3: str
    textColor2: str
    textColor4: str
    textColor1: str
    bgColor: str
    hasP3: bool


class PlayParams(BaseModel):
    id: str
    kind: str
    versionHash: str


class EditorialNotes(BaseModel):
    name: str
    standard: str
    short: str


class Attributes(BaseModel):
    lastModifiedDate: str
    supportsSing: bool
    description: Description
    artwork: Artwork
    playParams: PlayParams
    url: str
    hasCollaboration: bool
    curatorName: str
    audioTraits: List
    name: str
    isChart: bool
    playlistType: str
    editorialNotes: EditorialNotes
    artistName: Optional[str] = None


class Artwork1(BaseModel):
    width: int
    url: str
    height: int
    textColor3: str
    textColor2: str
    textColor4: str
    textColor1: str
    bgColor: str
    hasP3: bool


class PlayParams1(BaseModel):
    id: str
    kind: str


class Preview(BaseModel):
    url: str


class Attributes1(BaseModel):
    albumName: str
    hasTimeSyncedLyrics: bool
    genreNames: List[str]
    trackNumber: int
    releaseDate: str
    durationInMillis: int
    isVocalAttenuationAllowed: bool
    isMasteredForItunes: bool
    isrc: str
    artwork: Artwork1
    composerName: str
    audioLocale: str
    url: str
    playParams: PlayParams1
    discNumber: int
    hasCredits: bool
    hasLyrics: bool
    isAppleDigitalMaster: bool
    audioTraits: List[str]
    name: str
    previews: List[Preview]
    artistName: str


class Attributes2(BaseModel):
    name: str


class Datum2(BaseModel):
    id: str
    type: str
    href: str
    attributes: Attributes2


class Artists(BaseModel):
    href: str
    data: List[Datum2]


class Relationships1(BaseModel):
    artists: Artists


class Datum1(BaseModel):
    id: str
    type: str
    href: str
    attributes: Attributes1
    relationships: Relationships1


class Tracks(BaseModel):
    href: str
    next: Optional[str] = None
    data: List[Datum1]


class Relationships(BaseModel):
    tracks: Tracks


class Datum(BaseModel):
    id: str
    type: str
    href: str
    attributes: Attributes
    relationships: Relationships


class PlaylistMeta(BaseModel):
    data: List[Datum]
