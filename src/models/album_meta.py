from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


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


class Attributes(BaseModel):
    copyright: Optional[str] = None
    genreNames: List[Optional[str]] = None
    releaseDate: Optional[str] = None
    upc: Optional[str] = None
    isMasteredForItunes: Optional[bool] = None
    artwork: Artwork
    url: Optional[str] = None
    playParams: PlayParams
    recordLabel: Optional[str] = None
    isCompilation: Optional[bool] = None
    trackCount: Optional[int] = None
    isPrerelease: Optional[bool] = None
    audioTraits: List[Optional[str]] = None
    isSingle: Optional[bool] = None
    name: Optional[str] = None
    artistName: Optional[str] = None
    isComplete: Optional[bool] = None


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
    hasTimeSyncedLyrics: Optional[bool] = None
    albumName: Optional[str] = None
    genreNames: List[Optional[str]] = None
    trackNumber: Optional[int] = None
    durationInMillis: Optional[int] = None
    releaseDate: Optional[str] = None
    isVocalAttenuationAllowed: Optional[bool] = None
    isMasteredForItunes: Optional[bool] = None
    isrc: Optional[str] = None
    artwork: Artwork1
    composerName: Optional[str] = None
    audioLocale: Optional[str] = None
    playParams: PlayParams1
    url: Optional[str] = None
    discNumber: Optional[int] = None
    hasCredits: Optional[bool] = None
    isAppleDigitalMaster: Optional[bool] = None
    hasLyrics: Optional[bool] = None
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
    data: List[Datum1]


class Attributes3(BaseModel):
    name: Optional[str] = None


class Datum3(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: Optional[Attributes3] = None


class Artists1(BaseModel):
    href: Optional[str] = None
    data: List[Datum3]


class RecordLabels(BaseModel):
    href: Optional[str] = None
    data: List


class Relationships(BaseModel):
    tracks: Tracks
    artists: Artists1
    record_labels: RecordLabels = Field(..., alias='record-labels')


class Datum(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: Attributes
    relationships: Relationships


class AlbumMeta(BaseModel):
    data: List[Datum]
