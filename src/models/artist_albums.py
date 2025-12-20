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


class EditorialNotes(BaseModel):
    short: Optional[str] = None
    standard: Optional[str] = None
    name: Optional[str] = None


class Attributes(BaseModel):
    copyright: Optional[str] = None
    genreNames: List[str]
    releaseDate: Optional[str] = None
    isMasteredForItunes: Optional[bool] = None
    upc: Optional[str] = None
    artwork: Artwork
    url: Optional[str] = None
    playParams: Optional[PlayParams] = None
    recordLabel: Optional[str] = None
    trackCount: Optional[int] = None
    isCompilation: Optional[bool] = None
    isPrerelease: Optional[bool] = None
    audioTraits: List[str]
    isSingle: Optional[bool] = None
    name: Optional[str] = None
    artistName: Optional[str] = None
    isComplete: Optional[bool] = None
    editorialNotes: Optional[EditorialNotes] = None


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
    meta: Optional[Meta] = None


class ArtistAlbums(BaseModel):
    next: Optional[str] = None
    data: List[Datum]
