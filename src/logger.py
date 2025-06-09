import copy
from typing import Type

from creart import AbstractCreator, CreateTargetInfo, exists_module
from loguru import logger
from prompt_toolkit import print_formatted_text, ANSI


class GlobalLogger:
    def __init__(self):
        logger.remove()
        self.logger = copy.deepcopy(logger)
        self.logger.add(lambda msg: print_formatted_text(ANSI(msg), end=""), colorize=True,
                        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green>"
                               + " | <level>{level}</level>"
                               + " - <level>{message}</level>",
                        level="INFO")


class LoggerCreator(AbstractCreator):
    targets = (
        CreateTargetInfo("src.logger", "GlobalLogger"),
    )

    @staticmethod
    def available() -> bool:
        return exists_module("src.logger")

    @staticmethod
    def create(create_type: Type[GlobalLogger]) -> GlobalLogger:
        return create_type()


class RipLogger:
    item_type: str
    item_id: str
    full_name: str
    metadata: "SongMetadata"

    def __init__(self, _type: str, item_id: str):
        self.item_type = _type
        self.item_id = item_id
        logger.remove()
        self.logger = copy.deepcopy(logger)
        self.logger.add(lambda msg: print_formatted_text(ANSI(msg), end=""), colorize=True,
                        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green>"
                               + f" | <b>{self.item_type.upper()}</b>"
                               + f" | <b>{self.item_id}</b>"
                               + " | <level>{level}</level>"
                               + " - <level>{message}</level>",
                        level="INFO")

    def create(self):
        self.logger.info(f"Start ripping...")

    def set_fullname(self, artist: str, name: str = None):
        if not name:
            self.full_name = artist
        else:
            self.full_name = f"{artist} - {name}"
        self.logger.remove()
        self.logger.add(lambda msg: print_formatted_text(ANSI(msg), end=""), colorize=True,
                        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green>"
                               + f" | <b>{self.item_type.upper()}</b>"
                               + f" | <b>{self.full_name}</b>"
                               + " | <level>{level}</level>"
                               + " - <level>{message}</level>",
                        level="INFO")

    def not_exist(self):
        self.logger.error(
            f"Unable to download {self.item_type}. This {self.item_type} does not exist in all available storefronts")

    def already_exist(self):
        self.logger.info(f"Song already exists")

    def failed_lyrics(self):
        self.logger.warning("Failed to get lyrics")

    def audio_not_exist(self):
        self.logger.error("Failed to download song. Audio does not exist")

    def lossless_audio_not_exist(self):
        self.logger.error("Failed to download song. Lossless audio does not exist")

    def downloading(self):
        self.logger.info("Downloading song...")

    def decrypting(self):
        self.logger.info("Decrypting song...")

    def failed_integrity(self):
        self.logger.warning(f"Song did not pass the integrity check!")

    def saved(self):
        self.logger.success("Song saved!")

    def done(self):
        self.logger.success(f"Finished ripping")

    def selected_codec(self, selected_codec):
        self.logger.info(f"Selected codec: {selected_codec}")
