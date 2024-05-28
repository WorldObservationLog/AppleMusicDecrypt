from copy import deepcopy
from typing import Optional, Any

from loguru import logger

from src.url import URLType


class StatusCode:
    """
    For Song, available values are all.
    For others, available values are Waiting, Processing, Done and Failed.
    """
    Waiting = "WAITING"
    Processing = "PROCESSING"
    Parsing = "PARSING"
    Downloading = "DOWNLOADING"
    Decrypting = "DECRYPTING"
    Saving = "SAVING"
    Done = "Done"
    AlreadyExist = "ALREADY_EXIST"
    Failed = "FAILED"


class WarningCode:
    NoAvailableAccountForLyrics = "NO_AVAILABLE_ACCOUNT_FOR_LYRICS"
    UnableGetLyrics = "UNABLE_GET_LYRICS"
    RetryableDecryptFailed = "RETRYABLE_DECRYPT_FAILED"


class ErrorCode:
    NotExistInStorefront = "NOT_EXIST_IN_STOREFRONT"
    ForceModeM3U8NotExist = "FORCE_MODE_M3U8_NOT_EXIST"
    AudioNotExist = "AUDIO_NOT_EXIST"
    LosslessAudioNotExist = "LOSSLESS_AUDIO_NOT_EXIST"
    DecryptFailed = "DECRYPT_FAILED"


class BaseStatus:
    _type: str
    _current: str = StatusCode.Waiting
    _status_params: dict[str, Any] = {}
    _params: dict[str, Any] = {}
    _warning: str = ""
    _error: str = ""
    children = []

    def __init__(self, status_type: str):
        self._type = status_type

    def new(self, status_type):
        new_obj = deepcopy(self)
        new_obj._type = status_type
        new_obj._current = StatusCode
        new_obj._status_params = {}
        new_obj._params = {}
        new_obj._warning = ""
        new_obj._error = ""
        new_obj.children = []
        return new_obj

    def running(self):
        if self._error:
            return False
        if self._current == StatusCode.Waiting or self._current == StatusCode.Done or self._current == StatusCode.AlreadyExist:
            return False
        return True

    def set_status(self, status: str, **kwargs):
        self._current = status

    def get_status(self) -> str:
        return self._current

    def set_warning(self, warning: str, **kwargs):
        self._warning = warning

    def get_warning(self):
        return self._warning

    def set_error(self, error: str, **kwargs):
        self._error = error
        self._current = StatusCode.Failed

    def get_error(self):
        return self._error

    def set_progress(self, key: str, now: int, total: int, **kwargs):
        self._status_params[key] = {"now": now, "total": total}

    def get_progress(self, key: str) -> Optional[tuple[int, int]]:
        if self._status_params.get(key):
            return self._status_params[key]["now"], self._status_params[key]["total"]
        return None

    def set_param(self, **kwargs):
        for param in kwargs.items():
            self._params[param[0]] = param[1]


class LogStatus(BaseStatus):
    def _get_song_name(self) -> str:
        if self._params.get('title'):
            return f"{self._params.get('artist')} - {self._params.get('title')}"
        return self._params.get('artist')

    def set_status(self, status: str, **kwargs):
        super().set_status(status, **kwargs)
        match status:
            case StatusCode.Waiting:
                pass
            case StatusCode.Processing:
                if self._type == URLType.Song:
                    logger.debug(f"Task of {self._type} id {self._params.get('song_id')} was created")
                else:
                    logger.info(f"Ripping {self._type}: {self._get_song_name()}")
            case StatusCode.Parsing:
                logger.info(f"Ripping {self._type}: {self._get_song_name()}")
            case StatusCode.Downloading:
                logger.info(f"Downloading {self._type}: {self._get_song_name()}")
            case StatusCode.Decrypting:
                logger.info(f"Decrypting {self._type}: {self._get_song_name()}")
            case StatusCode.Saving:
                pass
            case StatusCode.Done:
                logger.info(
                    f"{self._type.capitalize()} {self._get_song_name()} saved!")
            case StatusCode.AlreadyExist:
                logger.info(
                    f"{self._type.capitalize()}: {self._get_song_name()} already exists")

    def set_warning(self, warning: str, **kwargs):
        super().set_warning(warning, **kwargs)
        match warning:
            case WarningCode.NoAvailableAccountForLyrics:
                logger.warning(f"No account is available for getting lyrics of storefront {self._params.get('song_storefront').upper()}. "
                               f"Use storefront {self._params.get('storefront').upper()} to get lyrics")
            case WarningCode.RetryableDecryptFailed:
                logger.warning(f"Failed to decrypt song: {self._get_song_name()}, {kwargs['action']}")
            case WarningCode.UnableGetLyrics:
                logger.warning(f"Unable to get lyrics of song: {self._get_song_name()}")

    def set_error(self, error: str, **kwargs):
        super().set_error(error, **kwargs)
        match error:
            case ErrorCode.AudioNotExist:
                logger.error(f"Failed to download song: {self._get_song_name()}. Audio does not exist")
            case ErrorCode.LosslessAudioNotExist:
                logger.error(f"Failed to download song: {self._get_song_name()}. Lossless audio does not exist")
            case ErrorCode.DecryptFailed:
                logger.error(f"Failed to decrypt song: {self._get_song_name()}")
            case ErrorCode.NotExistInStorefront:
                logger.error(
                    f"Unable to download {self._type} {self._get_song_name()}. "
                    f"This {self._type} does not exist in storefront {self._params.get('storefront').upper()} "
                    f"and no device is available to decrypt it")
            case ErrorCode.ForceModeM3U8NotExist:
                logger.error(f"Failed to get m3u8 from API for song: {self._get_song_name()}")