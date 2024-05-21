from typing import List, Optional

from pydantic import BaseModel


class PlayParams(BaseModel):
    id: Optional[str] = None
    kind: Optional[str] = None
    catalogId: Optional[str] = None
    displayType: Optional[int] = None


class Attributes(BaseModel):
    ttml: Optional[str] = None
    playParams: PlayParams


class Datum(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    attributes: Attributes


class SongLyrics(BaseModel):
    data: Optional[List[Datum]] = None
