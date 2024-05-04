from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


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


class Attributes(BaseModel):
    copyright: str
    genreNames: List[str]
    releaseDate: str
    upc: str
    isMasteredForItunes: bool
    artwork: Artwork
    url: str
    playParams: PlayParams
    recordLabel: str
    isCompilation: bool
    trackCount: int
    isPrerelease: bool
    audioTraits: List[str]
    isSingle: bool
    name: str
    artistName: str
    isComplete: bool


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
    hasTimeSyncedLyrics: bool
    albumName: str
    genreNames: List[str]
    trackNumber: int
    durationInMillis: int
    releaseDate: str
    isVocalAttenuationAllowed: bool
    isMasteredForItunes: bool
    isrc: str
    artwork: Artwork1
    composerName: str
    audioLocale: str
    playParams: PlayParams1
    url: str
    discNumber: int
    hasCredits: bool
    isAppleDigitalMaster: bool
    hasLyrics: bool
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
    data: List[Datum1]


class Attributes3(BaseModel):
    name: str


class Datum3(BaseModel):
    id: str
    type: str
    href: str
    attributes: Attributes3


class Artists1(BaseModel):
    href: str
    data: List[Datum3]


class RecordLabels(BaseModel):
    href: str
    data: List


class Relationships(BaseModel):
    tracks: Tracks
    artists: Artists1
    record_labels: RecordLabels = Field(..., alias='record-labels')


class Datum(BaseModel):
    id: str
    type: str
    href: str
    attributes: Attributes
    relationships: Relationships


class AlbumMeta(BaseModel):
    data: List[Datum]
