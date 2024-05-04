from typing import List

from pydantic import BaseModel


class PlayParams(BaseModel):
    id: str
    kind: str
    catalogId: str
    displayType: int


class Attributes(BaseModel):
    ttml: str
    playParams: PlayParams


class Datum(BaseModel):
    id: str
    type: str
    attributes: Attributes


class SongLyrics(BaseModel):
    data: List[Datum]
