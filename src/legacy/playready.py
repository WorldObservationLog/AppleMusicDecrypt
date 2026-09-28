import base64
import json
from functools import lru_cache

from pyplayready.cdm import Cdm
from pyplayready.device import Device
from pyplayready.system.pssh import PSSH

# PlayReady SL3000 device (.prd) stored as base64 text: {"device": "<base64>"}.
DEVICE_PATH = "assets/pr_device.json"


@lru_cache(maxsize=1)
def _load_device() -> Device:
    with open(DEVICE_PATH, encoding="utf-8") as f:
        return Device.loads(json.load(f)["device"])


def build_wrm_header(key_uri: str) -> tuple[str, str]:
    """Return ``(wrm_header, license_uri)`` for a PlayReady EXT-X-KEY URI.

    Apple uses either a ``data:text/plain;charset=UTF-16;base64,<PRO>``
    PlayReady Object, or a bare ``data:;base64,<kid>`` 16-byte key ID.
    """
    payload = key_uri.split("base64,", 1)[1] if "base64," in key_uri else key_uri.split(",", 1)[-1]
    if len(base64.b64decode(payload)) == 16:
        wrm_header = (
            '<WRMHEADER xmlns="http://schemas.microsoft.com/DRM/2007/03/PlayReadyHeader" version="4.0.0.0">'
            '<DATA><PROTECTINFO><KEYLEN>16</KEYLEN><ALGID>AESCTR</ALGID></PROTECTINFO>'
            f'<KID>{payload}</KID></DATA></WRMHEADER>'
        )
        return wrm_header, f"data:;base64,{payload}"
    headers = PSSH(payload).wrm_headers
    if not headers:
        raise RuntimeError("PlayReady PSSH contains no WRM header")
    return headers[0].dumps(), key_uri


class PlayReadyDecrypt:
    cdm: Cdm
    session_id: bytes

    def __init__(self):
        self.cdm = Cdm.from_device(_load_device())
        self.session_id = self.cdm.open()

    def generate_challenge(self, wrm_header: str) -> str:
        challenge = self.cdm.get_license_challenge(self.session_id, wrm_header)
        return base64.standard_b64encode(challenge.encode("utf-8")).decode()

    def generate_key(self, license_b64: str):
        self.cdm.parse_license(self.session_id, base64.b64decode(license_b64).decode("utf-8"))
        return self.cdm.get_keys(self.session_id)

    def close(self):
        self.cdm.close(self.session_id)
