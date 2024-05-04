from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class Description(BaseModel):
    standard: Optional[str] = None
    short: Optional[str] = None


class Artwork(BaseModel):
    width: Optional[int] = None
    url: Optional[str] = None
    height: Optional[int] = None
    textColor3: Optional[str] = None
    textColor2: Optional[str] = None
    textColor4: Optional[str] = None
    textColor1: Optional[str] = None
    bgColor: Optional[str] = None
    hasP3: Optional[bool] = None


class PlayParams(BaseModel):
    id: Optional[str] = None
    kind: Optional[str] = None
    versionHash: Optional[str] = None


class EditorialNotes(BaseModel):
    name: Optional[str] = None
    standard: Optional[str] = None
    short: Optional[str] = None


class Attributes(BaseModel):
    lastModifiedDate: Optional[str] = None
    supportsSing: Optional[bool] = None
    description: Description
    artwork: Artwork
    playParams: PlayParams
    url: Optional[str] = None
    hasCollaboration: Optional[bool] = None
    curatorName: Optional[str] = None
    audioTraits: List
    name: Optional[str] = None
    isChart: Optional[bool] = None
    playlistType: Optional[str] = None
    editorialNotes: EditorialNotes
    artistName: Optional[str] = None


class Artwork1(BaseModel):
    width: Optional[int] = None
    url: Optional[str] = None
    height: Optional[int] = None
    textColor3: Optional[str] = None
    textColor2: Optional[str] = None
    textColor4: Optional[str] = None
    textColor1: Optional[str] = None
    bgColor: Optional[str] = None
    hasP3: Optional[bool] = None


class PlayParams1(BaseModel):
    id: Optional[str] = None
    kind: Optional[str] = None


class Preview(BaseModel):
    url: Optional[str] = None


class Attributes1(BaseModel):
    albumName: Optional[str] = None
    hasTimeSyncedLyrics: Optional[bool] = None
    genreNames: List[Optional[str]] = None
    trackNumber: Optional[int] = None
    releaseDate: Optional[str] = None
    durationInMillis: Optional[int] = None
    isVocalAttenuationAllowed: Optional[bool] = None
    isMasteredForItunes: Optional[bool] = None
    isrc: Optional[str] = None
    artwork: Artwork1
    composerName: Optional[str] = None
    audioLocale: Optional[str] = None
    url: Optional[str] = None
    playParams: PlayParams1
    discNumber: Optional[int] = None
    hasCredits: Optional[bool] = None
    hasLyrics: Optional[bool] = None
    isAppleDigitalMaster: Optional[bool] = None
    audioTraits: List[Optional[str]] = None
    name: Optional[str] = None
    previews: List[Preview]
    artistName: Optional[str] = None


class Attributes2(BaseModel):
    name: Optional[str] = None


class Datum2(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: Attributes2


class Artists(BaseModel):
    href: Optional[str] = None
    data: List[Datum2]


class Relationships1(BaseModel):
    artists: Artists


class Datum1(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: Attributes1
    relationships: Relationships1


class Tracks(BaseModel):
    href: Optional[str] = None
    next: Optional[Optional[str]] = None
    data: List[Datum1]


class Relationships(BaseModel):
    tracks: Tracks


class Datum(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: Attributes
    relationships: Relationships


class PlaylistMeta(BaseModel):
    data: List[Datum]
