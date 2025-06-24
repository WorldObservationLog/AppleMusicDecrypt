from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


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


class Preview(BaseModel):
    url: Optional[str] = None


class Attributes(BaseModel):
    albumName: Optional[str] = None
    hasTimeSyncedLyrics: Optional[bool] = None
    genreNames: List[Optional[str]]  = None
    trackNumber: Optional[int] = None
    releaseDate: Optional[str] = None
    durationInMillis: Optional[int] = None
    isVocalAttenuationAllowed: Optional[bool] = None
    isMasteredForItunes: Optional[bool] = None
    isrc: Optional[str] = None
    artwork: Artwork
    composerName: Optional[str] = None
    audioLocale: Optional[str] = None
    url: Optional[str] = None
    playParams: PlayParams
    discNumber: Optional[int] = None
    hasLyrics: Optional[bool] = None
    isAppleDigitalMaster: Optional[bool] = None
    audioTraits: List[Optional[str]] = None
    attribution: Optional[str] = None
    name: Optional[str] = None
    previews: List[Preview]
    artistName: Optional[str] = None


class ContentVersion(BaseModel):
    MZ_INDEXER: Optional[int] = None
    RTCI: Optional[int] = None


class Meta(BaseModel):
    contentVersion: ContentVersion


class Datum(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: Attributes
    meta: Meta


class AlbumTracks(BaseModel):
    next: Optional[Optional[str]] = None
    data: Optional[List[Datum]] = None
