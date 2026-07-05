from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class Description(BaseModel):
    standard: Optional[str] = None


class Artwork(BaseModel):
    width: Optional[int] = None
    height: Optional[int] = None
    url: Optional[str] = None
    hasP3: Optional[bool] = None


class PlayParams(BaseModel):
    id: Optional[str] = None
    kind: Optional[str] = None
    versionHash: Optional[str] = None


class Attributes(BaseModel):
    hasCollaboration: Optional[bool] = None
    curatorName: Optional[str] = None
    lastModifiedDate: Optional[str] = None
    audioTraits: Optional[List] = None
    name: Optional[str] = None
    isChart: Optional[bool] = None
    supportsSing: Optional[bool] = None
    playlistType: Optional[str] = None
    description: Optional[Description] = None
    artwork: Optional[Artwork] = None
    playParams: Optional[PlayParams] = None
    url: Optional[str] = None


class Datum1(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None


class Curator(BaseModel):
    href: Optional[str] = None
    data: Optional[List[Datum1]] = None


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
    genreNames: Optional[List[str]] = None
    trackNumber: Optional[int] = None
    releaseDate: Optional[str] = None
    durationInMillis: Optional[int] = None
    isVocalAttenuationAllowed: Optional[bool] = None
    isMasteredForItunes: Optional[bool] = None
    isrc: Optional[str] = None
    artwork: Optional[Artwork1] = None
    composerName: Optional[str] = None
    audioLocale: Optional[str] = None
    url: Optional[str] = None
    playParams: Optional[PlayParams1] = None
    discNumber: Optional[int] = None
    hasCredits: Optional[bool] = None
    isAppleDigitalMaster: Optional[bool] = None
    hasLyrics: Optional[bool] = None
    audioTraits: Optional[List[str]] = None
    name: Optional[str] = None
    previews: Optional[List[Preview]] = None
    artistName: Optional[str] = None


class ContentVersion(BaseModel):
    RTCI: Optional[int] = None
    MZ_INDEXER: Optional[int] = None


class Meta(BaseModel):
    contentVersion: Optional[ContentVersion] = None


class Datum2(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: Attributes1
    meta: Optional[Meta] = None


class Tracks(BaseModel):
    href: Optional[str] = None
    next: Optional[str] = None
    data: Optional[List[Datum2]] = None


class Relationships(BaseModel):
    curator: Optional[Curator] = None
    tracks: Tracks


class Datum(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: Attributes
    relationships: Relationships


class PlaylistInfo(BaseModel):
    data: List[Datum]
    songIdIndexMapping: dict[str, int] = {}
