class FridaNotExistException(Exception):
    ...


class FridaNotRunningException(Exception):
    ...


class ADBConnectException(Exception):
    ...


class FailedGetAuthParamException(Exception):
    ...


class DecryptException(Exception):
    ...


class NotTimeSyncedLyricsException(Exception):
    ...


class CodecNotFoundException(Exception):
    ...


class RetryableDecryptException(Exception):
    ...

class FailedGetM3U8FromDeviceException(Exception):
    ...

class SongNotPassIntegrityCheckException(Exception):
    ...