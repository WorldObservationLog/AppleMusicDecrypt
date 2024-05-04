from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


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


class Preview(BaseModel):
    url: str


class Attributes(BaseModel):
    hasTimeSyncedLyrics: bool
    albumName: str
    genreNames: List[str]
    trackNumber: int
    releaseDate: str
    durationInMillis: int
    isVocalAttenuationAllowed: bool
    isMasteredForItunes: bool
    isrc: str
    artwork: Artwork
    composerName: Optional[str] = None
    audioLocale: str
    url: str
    playParams: PlayParams
    discNumber: int
    hasCredits: bool
    isAppleDigitalMaster: bool
    hasLyrics: bool
    audioTraits: List[str]
    name: str
    previews: List[Preview]
    artistName: str


class Datum(BaseModel):
    id: str
    type: str
    href: str
    attributes: Attributes


class TracksMeta(BaseModel):
    next: Optional[str] = None
    data: List[Datum]
