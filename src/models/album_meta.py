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
    artwork: Optional[Artwork]
    url: Optional[str] = None
    playParams: Optional[PlayParams] = None
    recordLabel: Optional[str] = None
    isCompilation: Optional[bool] = None
    trackCount: Optional[int] = None
    isPrerelease: Optional[bool] = None
    audioTraits: List[Optional[str]] = None
    isSingle: Optional[bool] = None
    name: Optional[str] = None
    artistName: Optional[str] = None
    isComplete: Optional[bool] = None
    contentRating: Optional[str] = None


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
    artwork: Optional[Artwork1] = None
    composerName: Optional[str] = None
    audioLocale: Optional[str] = None
    playParams: Optional[PlayParams1] = None
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
    attributes: Optional[Attributes2] = None


class Artists(BaseModel):
    href: Optional[str] = None
    data: Optional[List[Datum2]] = None


class Relationships1(BaseModel):
    artists: Optional[Artists] = None


class Datum1(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: Optional[Attributes1]
    relationships: Optional[Relationships1] = None


class Tracks(BaseModel):
    href: Optional[str] = None
    next: Optional[str] = None
    data: List[Datum1] = None


class Attributes3(BaseModel):
    name: Optional[str] = None


class Datum3(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: Optional[Attributes3] = None


class Artists1(BaseModel):
    href: Optional[str] = None
    data: List[Datum3] = None


class RecordLabels(BaseModel):
    href: Optional[str] = None
    data: Optional[list] = None


class Relationships(BaseModel):
    tracks: Optional[Tracks] = None
    artists: Optional[Artists1] = None
    record_labels: Optional[RecordLabels] = Field(..., alias='record-labels')


class ContentVersion(BaseModel):
    MZ_INDEXER: Optional[int] = None
    RTCI: Optional[int] = None


class Meta(BaseModel):
    contentVersion: Optional[ContentVersion] = None


class Datum(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: Optional[Attributes] = None
    relationships: Optional[Relationships] = None
    meta: Optional[Meta] = None


class AlbumMeta(BaseModel):
    data: List[Datum]
