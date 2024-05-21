import asyncio
import json
import subprocess
from typing import Optional

import frida
import regex
from loguru import logger
from ppadb.client import Client as AdbClient
from ppadb.device import Device as AdbDevice

from src.exceptions import ADBConnectException, FailedGetAuthParamException, \
    FridaNotRunningException
from src.types import AuthParams


class Device:
    host: str
    client: AdbClient
    device: AdbDevice
    fridaPort: int
    fridaDevice: frida.core.Device = None
    fridaSession: frida.core.Session = None
    pid: int
    authParams: AuthParams = None
    suMethod: str
    decryptLock: asyncio.Lock

    def __init__(self, host="127.0.0.1", port=5037, su_method: str = "su -c"):
        self.client = AdbClient(host, port)
        self.suMethod = su_method
        self.host = host
        self.decryptLock = asyncio.Lock()

    def connect(self, host: str, port: int):
        try:
            status = self.client.remote_connect(host, port)
        except RuntimeError:
            subprocess.run("adb devices", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            status = self.client.remote_connect(host, port)
        if not status:
            raise ADBConnectException
        self.device = self.client.device(f"{host}:{port}")

    def _execute_command(self, cmd: str, su: bool = False, sh: bool = False) -> Optional[str]:
        whoami = self.device.shell("whoami")
        final_cmd = cmd
        if whoami.strip() != "root" and su:
            if self.suMethod == "su -c":
                replaced_cmd = cmd.replace("\"", "\\\"")
                final_cmd = f"{self.suMethod} \"{replaced_cmd}\""
            else:
                final_cmd = f"{self.suMethod} {final_cmd}"
        if sh:
            final_cmd = f"sh -c '{final_cmd}'"
        output = self.device.shell(final_cmd, timeout=30)
        if not output:
            return ""
        return output

    def _if_frida_running(self) -> bool:
        logger.debug("checking if frida-server running")
        output = self._execute_command("ps -e | grep frida")
        if not output or "frida" not in output:
            return False
        return True

    def _start_forward(self, local_port: int, remote_port: int):
        self.device.forward(f"tcp:{local_port}", f"tcp:{remote_port}")

    def _inject_frida(self, frida_port):
        logger.debug("injecting agent script")
        self.fridaPort = frida_port
        with open("agent.js", "r") as f:
            agent = f.read().replace("2147483647", str(frida_port))
        if not self.fridaDevice:
            frida.get_device_manager().add_remote_device(self.device.serial)
            self.fridaDevice = frida.get_device_manager().get_device(self.device.serial)
        self.pid = self.fridaDevice.spawn("com.apple.android.music")
        self.fridaSession = self.fridaDevice.attach(self.pid)
        script: frida.core.Script = self.fridaSession.create_script(agent)
        script.load()
        self.fridaDevice.resume(self.pid)

    def restart_inject_frida(self):
        self.fridaSession.detach()
        self.fridaDevice.kill(self.pid)
        self._inject_frida(self.fridaPort)

    def start_inject_frida(self, frida_port):
        if not self._if_frida_running():
            # self._start_remote_frida()
            raise FridaNotRunningException
        self._start_forward(frida_port, frida_port)
        self._inject_frida(frida_port)

    def _get_dsid(self) -> str:
        logger.debug("getting dsid")
        dsid = self._execute_command(
            "sqlite3 /data/data/com.apple.android.music/files/mpl_db/cookies.sqlitedb \"select value from cookies where name='X-Dsid';\"",
            True)
        if not dsid:
            raise FailedGetAuthParamException
        return dsid.strip()

    def _get_account_token(self, dsid: str) -> str:
        logger.debug("getting account token")
        account_token = self._execute_command(
            f"sqlite3 /data/data/com.apple.android.music/files/mpl_db/cookies.sqlitedb \"select value from cookies where name='mz_at_ssl-{dsid}';\"",
            True)
        if not account_token:
            raise FailedGetAuthParamException
        return account_token.strip()

    def _get_access_token(self) -> str:
        logger.debug("getting access token")
        prefs = self._execute_command("cat /data/data/com.apple.android.music/shared_prefs/preferences.xml", True)
        match = regex.search(r"eyJr[^<]*", prefs)
        if not match:
            raise FailedGetAuthParamException
        return match[0]

    def _get_storefront(self) -> str | None:
        logger.debug("getting storefront")
        storefront_id = self._execute_command(
            "sqlite3 /data/data/com.apple.android.music/files/mpl_db/accounts.sqlitedb \"select storeFront from account;\"",
            True)
        if not storefront_id:
            raise FailedGetAuthParamException
        with open("assets/storefront_ids.json", encoding="utf-8") as f:
            storefront_ids = json.load(f)
        for storefront_mapping in storefront_ids:
            if storefront_mapping["storefrontId"] == int(storefront_id.split("-")[0]):
                return storefront_mapping["code"]
        return None

    def get_auth_params(self):
        if not self.authParams:
            dsid = self._get_dsid()
            token = self._get_account_token(dsid)
            access_token = self._get_access_token()
            storefront = self._get_storefront()
            self.authParams = AuthParams(dsid=dsid, accountToken=token,
                                         accountAccessToken=access_token, storefront=storefront)
        return self.authParams
