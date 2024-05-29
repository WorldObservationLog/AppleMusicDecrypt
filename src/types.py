from typing import Optional, Any

from pydantic import BaseModel

defaultId = "0"
prefetchKey = "skd://itunes.apple.com/P000000000/s1/e1"


class SampleInfo(BaseModel):
    data: bytes
    duration: int
    descIndex: int


class SongInfo(BaseModel):
    codec: str
    raw: bytes
    samples: list[SampleInfo]
    nhml: str
    decoderParams: Optional[bytes] = None
    params: dict[str, Any]


class Codec:
    ALAC = "alac"
    EC3 = "ec3"
    AC3 = "ac3"
    AAC_BINAURAL = "aac-binaural"
    AAC_DOWNMIX = "aac-downmix"
    AAC = "aac"


class CodecKeySuffix:
    KeySuffixAtmos = "c24"
    KeySuffixAlac = "c23"
    KeySuffixAAC = "c22"
    KeySuffixAACDownmix = "c24"
    KeySuffixAACBinaural = "c24"
    KeySuffixDefault = "c6"


class CodecRegex:
    RegexCodecAtmos = "audio-(atmos|ec3)-\\d{4}$"
    RegexCodecAC3 = "audio-ac3-\\d{3}$"
    RegexCodecAlac = "audio-alac-stereo-\\d{5,6}-\\d{2}$"
    RegexCodecBinaural = "audio-stereo-\\d{3}-binaural$"
    RegexCodecDownmix = "audio-stereo-\\d{3}-downmix$"
    RegexCodecAAC = "audio-stereo-\\d{3}$"

    @classmethod
    def get_pattern_by_codec(cls, codec: str):
        codec_pattern_mapping = {Codec.ALAC: cls.RegexCodecAlac, Codec.EC3: cls.RegexCodecAtmos,
                                 Codec.AAC_DOWNMIX: cls.RegexCodecDownmix, Codec.AAC_BINAURAL: cls.RegexCodecBinaural,
                                 Codec.AAC: cls.RegexCodecAAC, Codec.AC3: cls.RegexCodecAC3}
        return codec_pattern_mapping.get(codec)


class AuthParams(BaseModel):
    dsid: str
    accountToken: str
    accountAccessToken: str
    storefront: str


class GlobalAuthParams(AuthParams):
    anonymousAccessToken: str

    @classmethod
    def from_auth_params_and_token(cls, auth_params: AuthParams, token: str):
        return cls(dsid=auth_params.dsid, accountToken=auth_params.accountToken, anonymousAccessToken=token,
                   accountAccessToken=auth_params.accountAccessToken, storefront=auth_params.storefront)
