from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar, Iterable, Optional, Union

DESCRIPTOR: _descriptor.FileDescriptor

class WidevineHeader(_message.Message):
    __slots__ = ["content_id", "key_ids", "provider"]
    CONTENT_ID_FIELD_NUMBER: ClassVar[int]
    KEY_IDS_FIELD_NUMBER: ClassVar[int]
    PROVIDER_FIELD_NUMBER: ClassVar[int]
    content_id: bytes
    key_ids: _containers.RepeatedScalarFieldContainer[str]
    provider: str
    def __init__(self, key_ids: Optional[Iterable[str]] = ..., provider: Optional[str] = ..., content_id: Optional[bytes] = ...) -> None: ...

class WidevinePsshData(_message.Message):
    __slots__ = ["algorithm", "content_id", "crypto_period_index", "grouped_license", "key_id", "policy", "protection_scheme", "provider"]
    class Algorithm(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = []
    AESCTR: WidevinePsshData.Algorithm
    ALGORITHM_FIELD_NUMBER: ClassVar[int]
    CONTENT_ID_FIELD_NUMBER: ClassVar[int]
    CRYPTO_PERIOD_INDEX_FIELD_NUMBER: ClassVar[int]
    GROUPED_LICENSE_FIELD_NUMBER: ClassVar[int]
    KEY_ID_FIELD_NUMBER: ClassVar[int]
    POLICY_FIELD_NUMBER: ClassVar[int]
    PROTECTION_SCHEME_FIELD_NUMBER: ClassVar[int]
    PROVIDER_FIELD_NUMBER: ClassVar[int]
    UNENCRYPTED: WidevinePsshData.Algorithm
    algorithm: WidevinePsshData.Algorithm
    content_id: bytes
    crypto_period_index: int
    grouped_license: bytes
    key_id: _containers.RepeatedScalarFieldContainer[bytes]
    policy: str
    protection_scheme: int
    provider: str
    def __init__(self, algorithm: Optional[Union[WidevinePsshData.Algorithm, str]] = ..., key_id: Optional[Iterable[bytes]] = ..., provider: Optional[str] = ..., content_id: Optional[bytes] = ..., policy: Optional[str] = ..., crypto_period_index: Optional[int] = ..., grouped_license: Optional[bytes] = ..., protection_scheme: Optional[int] = ...) -> None: ...
