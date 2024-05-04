from __future__ import annotations

from typing import List

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


class ExtendedAssetUrls(BaseModel):
    plus: str
    lightweight: str
    superLightweight: str
    lightweightPlus: str
    enhancedHls: str


class Attributes(BaseModel):
    hasTimeSyncedLyrics: bool
    albumName: str
    genreNames: List[str]
    trackNumber: int
    durationInMillis: int
    releaseDate: str
    isVocalAttenuationAllowed: bool
    isMasteredForItunes: bool
    isrc: str
    artwork: Artwork
    composerName: str
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
    extendedAssetUrls: ExtendedAssetUrls


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


class Attributes1(BaseModel):
    copyright: str
    genreNames: List[str]
    releaseDate: str
    isMasteredForItunes: bool
    upc: str
    artwork: Artwork1
    url: str
    playParams: PlayParams1
    recordLabel: str
    isCompilation: bool
    trackCount: int
    isPrerelease: bool
    audioTraits: List[str]
    isSingle: bool
    name: str
    artistName: str
    isComplete: bool


class Datum1(BaseModel):
    id: str
    type: str
    href: str
    attributes: Attributes1


class Albums(BaseModel):
    href: str
    data: List[Datum1]


class Datum2(BaseModel):
    id: str
    type: str
    href: str


class Artists(BaseModel):
    href: str
    data: List[Datum2]


class Relationships(BaseModel):
    albums: Albums
    artists: Artists


class Datum(BaseModel):
    id: str
    type: str
    href: str
    attributes: Attributes
    relationships: Relationships


class SongData(BaseModel):
    data: List[Datum]
