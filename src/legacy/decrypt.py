import base64
from pywidevine import PSSH, Device, Cdm

from src.legacy.pssh import generate_pssh


class WidevineDecrypt:
    device: Device
    cdm: Cdm
    session_id: bytes

    def __init__(self):
        self.device = Device.load("assets/device.wvd")
        self.cdm = Cdm.from_device(self.device)
        self.session_id = self.cdm.open()

    def generate_challenge(self, kid: str):
        pssh = PSSH(generate_pssh(kid))
        challenge = self.cdm.get_license_challenge(self.session_id, pssh)
        return base64.standard_b64encode(challenge).decode()

    def generate_key(self, license: str):
        self.cdm.parse_license(self.session_id, license)
        return self.cdm.get_keys(self.session_id)
