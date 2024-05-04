import asyncio
import logging
import sys

from prompt_toolkit.shortcuts import ProgressBar
from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, before_sleep_log

from src.adb import Device
from src.exceptions import DecryptException
from src.models.song_data import Datum
from src.mp4 import SongInfo, SampleInfo
from src.types import defaultId, prefetchKey


async def decrypt(info: SongInfo, keys: list[str], manifest: Datum, device: Device) -> bytes:
    async with device.decryptLock:
        logger.info(f"Decrypting song: {manifest.attributes.artistName} - {manifest.attributes.name}")
        reader, writer = await asyncio.open_connection(device.host, device.fridaPort)
        decrypted = bytes()
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
            result = await decrypt_sample(writer, reader, sample)
            decrypted += result
        writer.write(bytes([0, 0, 0, 0]))
        writer.close()
        return decrypted


async def decrypt_sample(writer: asyncio.StreamWriter, reader: asyncio.StreamReader, sample: SampleInfo) -> bytes:
    writer.write(len(sample.data).to_bytes(4, byteorder="little", signed=False))
    writer.write(sample.data)
    result = await reader.read(len(sample.data))
    if not result:
        raise DecryptException
    return result
