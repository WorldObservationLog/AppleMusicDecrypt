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


class Attributes(BaseModel):
    genreNames: List[Optional[str]] = None
    name: Optional[str] = None
    artwork: Artwork
    classicalUrl: Optional[str] = None
    url: Optional[str] = None


class Datum1(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None


class Albums(BaseModel):
    href: Optional[str] = None
    next: Optional[str] = None
    data: List[Datum1]


class Relationships(BaseModel):
    albums: Albums


class Datum(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: Attributes
    relationships: Relationships


class ArtistInfo(BaseModel):
    data: List[Datum]
