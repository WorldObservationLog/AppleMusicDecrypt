import asyncio
import logging

from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, before_sleep_log

from src.adb import Device, HyperDecryptDevice
from src.exceptions import DecryptException, RetryableDecryptException
from src.models.song_data import Datum
from src.mp4 import SongInfo, SampleInfo
from src.types import defaultId, prefetchKey
from src.utils import timeit

retry_count = {}


@retry(retry=retry_if_exception_type(RetryableDecryptException), stop=stop_after_attempt(3),
       before_sleep=before_sleep_log(logger, logging.WARN))
@timeit
async def decrypt(info: SongInfo, keys: list[str], manifest: Datum, device: Device | HyperDecryptDevice) -> bytes:
    async with device.decryptLock:
        if isinstance(device, HyperDecryptDevice):
            logger.info(f"Using hyperDecryptDevice {device.serial} to decrypt song: {manifest.attributes.artistName} - {manifest.attributes.name}")
        else:
            logger.info(f"Using device {device.serial} to decrypt song: {manifest.attributes.artistName} - {manifest.attributes.name}")
        try:
            reader, writer = await asyncio.open_connection(device.host, device.fridaPort, limit=2**14)
        except ConnectionRefusedError:
            logger.warning(f"Failed to connect to device {device.serial}, re-injecting")
            device.restart_inject_frida()
            raise RetryableDecryptException
        decrypted = []
        last_index = 255
        for sample in info.samples:
            if last_index != sample.descIndex:
                if len(decrypted) != 0:
                    writer.write(bytes([0, 0, 0, 0]))
                key_uri = keys[sample.descIndex]
                track_id = manifest.id
                if key_uri == prefetchKey:
                    track_id = defaultId
                writer.write(bytes([len(track_id)]))
                writer.write(track_id.encode("utf-8"))
                writer.write(bytes([len(key_uri)]))
                writer.write(key_uri.encode("utf-8"))
            last_index = sample.descIndex
            try:
                result = await decrypt_sample(writer, reader, sample)
            except RetryableDecryptException as e:
                if 0 <= retry_count.get(device.serial, 0) < 3 or 4 <= retry_count.get(device.serial, 0) < 6:
                    logger.warning(f"Failed to decrypt song: {manifest.attributes.artistName} - {manifest.attributes.name}, retrying")
                    writer.write(bytes([0, 0, 0, 0]))
                    writer.close()
                    raise e
                elif retry_count == 3:
                    logger.warning(f"Failed to decrypt song: {manifest.attributes.artistName} - {manifest.attributes.name}, re-injecting")
                    device.restart_inject_frida()
                    raise e
                else:
                    logger.error(f"Failed to decrypt song: {manifest.attributes.artistName} - {manifest.attributes.name}")
                    raise DecryptException
            decrypted.append(result)
        writer.write(bytes([0, 0, 0, 0]))
        writer.close()
        return bytes().join(decrypted)


async def decrypt_sample(writer: asyncio.StreamWriter, reader: asyncio.StreamReader, sample: SampleInfo) -> bytes:
    writer.write(len(sample.data).to_bytes(4, byteorder="little", signed=False))
    writer.write(sample.data)
    result = await reader.read(len(sample.data))
    if not result:
        raise RetryableDecryptException
    return result
