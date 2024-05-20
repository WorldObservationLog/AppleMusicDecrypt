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


class ExtendedAssetUrls(BaseModel):
    plus: Optional[str] = None
    lightweight: Optional[str] = None
    superLightweight: Optional[str] = None
    lightweightPlus: Optional[str] = None
    enhancedHls: Optional[str] = None


class Attributes(BaseModel):
    hasTimeSyncedLyrics: Optional[bool] = None
    albumName: Optional[str] = None
    genreNames: List[Optional[str]] = None
    trackNumber: Optional[int] = None
    durationInMillis: Optional[int] = None
    releaseDate: Optional[str] = None
    isVocalAttenuationAllowed: Optional[bool] = None
    isMasteredForItunes: Optional[bool] = None
    isrc: Optional[str] = None
    artwork: Artwork
    composerName: Optional[str] = None
    audioLocale: Optional[str] = None
    url: Optional[str] = None
    playParams: Optional[PlayParams] = None
    discNumber: Optional[int] = None
    hasCredits: Optional[bool] = None
    isAppleDigitalMaster: Optional[bool] = None
    hasLyrics: Optional[bool] = None
    audioTraits: List[Optional[str]] = None
    name: Optional[str] = None
    previews: List[Preview]
    artistName: Optional[str] = None
    extendedAssetUrls: Optional[ExtendedAssetUrls] = None
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


class Attributes1(BaseModel):
    copyright: Optional[str] = None
    genreNames: List[Optional[str]] = None
    releaseDate: Optional[str] = None
    isMasteredForItunes: Optional[bool] = None
    upc: Optional[str] = None
    artwork: Artwork1
    url: Optional[str] = None
    playParams: Optional[PlayParams1] = None
    recordLabel: Optional[str] = None
    isCompilation: Optional[bool] = None
    trackCount: Optional[int] = None
    isPrerelease: Optional[bool] = None
    audioTraits: List[Optional[str]] = None
    isSingle: Optional[bool] = None
    name: Optional[str] = None
    artistName: Optional[str] = None
    isComplete: Optional[bool] = None


class Datum1(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: Attributes1


class Albums(BaseModel):
    href: Optional[str] = None
    data: List[Datum1]


class Datum2(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None


class Artists(BaseModel):
    href: Optional[str] = None
    data: List[Datum2]


class Relationships(BaseModel):
    albums: Albums
    artists: Artists


class Datum(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: Attributes
    relationships: Relationships


class SongData(BaseModel):
    data: List[Datum]
