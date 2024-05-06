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
    hasP3: bool


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
    isMasteredForItunes: bool
    upc: Optional[str] = None
    artwork: Artwork
    url: Optional[str] = None
    playParams: PlayParams
    recordLabel: Optional[str] = None
    trackCount: Optional[int] = None
    isCompilation: bool
    isPrerelease: bool
    audioTraits: List[str]
    isSingle: bool
    name: Optional[str] = None
    artistName: Optional[str] = None
    isComplete: bool
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
    meta: Meta


class ArtistAlbums(BaseModel):
    next: Optional[str] = None
    data: List[Datum]
