from google.protobuf import empty_pb2 as _empty_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ReplyHeader(_message.Message):
    __slots__ = ("code", "msg")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MSG_FIELD_NUMBER: _ClassVar[int]
    code: int
    msg: str
    def __init__(self, code: _Optional[int] = ..., msg: _Optional[str] = ...) -> None: ...

class StatusReply(_message.Message):
    __slots__ = ("header", "data")
    HEADER_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    header: ReplyHeader
    data: StatusData
    def __init__(self, header: _Optional[_Union[ReplyHeader, _Mapping]] = ..., data: _Optional[_Union[StatusData, _Mapping]] = ...) -> None: ...

class StatusData(_message.Message):
    __slots__ = ("status", "regions", "client_count")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    REGIONS_FIELD_NUMBER: _ClassVar[int]
    CLIENT_COUNT_FIELD_NUMBER: _ClassVar[int]
    status: bool
    regions: _containers.RepeatedScalarFieldContainer[str]
    client_count: int
    def __init__(self, status: bool = ..., regions: _Optional[_Iterable[str]] = ..., client_count: _Optional[int] = ...) -> None: ...

class LoginRequest(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: LoginData
    def __init__(self, data: _Optional[_Union[LoginData, _Mapping]] = ...) -> None: ...

class LoginData(_message.Message):
    __slots__ = ("username", "password", "two_step_code")
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    PASSWORD_FIELD_NUMBER: _ClassVar[int]
    TWO_STEP_CODE_FIELD_NUMBER: _ClassVar[int]
    username: str
    password: str
    two_step_code: int
    def __init__(self, username: _Optional[str] = ..., password: _Optional[str] = ..., two_step_code: _Optional[int] = ...) -> None: ...

class LoginReply(_message.Message):
    __slots__ = ("header",)
    HEADER_FIELD_NUMBER: _ClassVar[int]
    header: ReplyHeader
    def __init__(self, header: _Optional[_Union[ReplyHeader, _Mapping]] = ...) -> None: ...

class DecryptRequest(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: DecryptData
    def __init__(self, data: _Optional[_Union[DecryptData, _Mapping]] = ...) -> None: ...

class DecryptReply(_message.Message):
    __slots__ = ("header", "data")
    HEADER_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    header: ReplyHeader
    data: DecryptData
    def __init__(self, header: _Optional[_Union[ReplyHeader, _Mapping]] = ..., data: _Optional[_Union[DecryptData, _Mapping]] = ...) -> None: ...

class DecryptData(_message.Message):
    __slots__ = ("adam_id", "key", "sample_index", "sample")
    ADAM_ID_FIELD_NUMBER: _ClassVar[int]
    KEY_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_INDEX_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_FIELD_NUMBER: _ClassVar[int]
    adam_id: str
    key: str
    sample_index: int
    sample: bytes
    def __init__(self, adam_id: _Optional[str] = ..., key: _Optional[str] = ..., sample_index: _Optional[int] = ..., sample: _Optional[bytes] = ...) -> None: ...

class M3U8Request(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: M3U8DataRequest
    def __init__(self, data: _Optional[_Union[M3U8DataRequest, _Mapping]] = ...) -> None: ...

class M3U8DataRequest(_message.Message):
    __slots__ = ("adam_id",)
    ADAM_ID_FIELD_NUMBER: _ClassVar[int]
    adam_id: str
    def __init__(self, adam_id: _Optional[str] = ...) -> None: ...

class M3U8Reply(_message.Message):
    __slots__ = ("header", "data")
    HEADER_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    header: ReplyHeader
    data: M3U8DataResponse
    def __init__(self, header: _Optional[_Union[ReplyHeader, _Mapping]] = ..., data: _Optional[_Union[M3U8DataResponse, _Mapping]] = ...) -> None: ...

class M3U8DataResponse(_message.Message):
    __slots__ = ("adam_id", "m3u8")
    ADAM_ID_FIELD_NUMBER: _ClassVar[int]
    M3U8_FIELD_NUMBER: _ClassVar[int]
    adam_id: str
    m3u8: str
    def __init__(self, adam_id: _Optional[str] = ..., m3u8: _Optional[str] = ...) -> None: ...

class LyricsRequest(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: LyricsDataRequest
    def __init__(self, data: _Optional[_Union[LyricsDataRequest, _Mapping]] = ...) -> None: ...

class LyricsDataRequest(_message.Message):
    __slots__ = ("adam_id", "region", "language")
    ADAM_ID_FIELD_NUMBER: _ClassVar[int]
    REGION_FIELD_NUMBER: _ClassVar[int]
    LANGUAGE_FIELD_NUMBER: _ClassVar[int]
    adam_id: str
    region: str
    language: str
    def __init__(self, adam_id: _Optional[str] = ..., region: _Optional[str] = ..., language: _Optional[str] = ...) -> None: ...

class LyricsReply(_message.Message):
    __slots__ = ("header", "data")
    HEADER_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    header: ReplyHeader
    data: LyricsDataResponse
    def __init__(self, header: _Optional[_Union[ReplyHeader, _Mapping]] = ..., data: _Optional[_Union[LyricsDataResponse, _Mapping]] = ...) -> None: ...

class LyricsDataResponse(_message.Message):
    __slots__ = ("adam_id", "lyrics")
    ADAM_ID_FIELD_NUMBER: _ClassVar[int]
    LYRICS_FIELD_NUMBER: _ClassVar[int]
    adam_id: str
    lyrics: str
    def __init__(self, adam_id: _Optional[str] = ..., lyrics: _Optional[str] = ...) -> None: ...

class ErrorReply(_message.Message):
    __slots__ = ("header",)
    HEADER_FIELD_NUMBER: _ClassVar[int]
    header: ReplyHeader
    def __init__(self, header: _Optional[_Union[ReplyHeader, _Mapping]] = ...) -> None: ...
