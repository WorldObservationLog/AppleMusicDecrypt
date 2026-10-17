import copy
import sys
import urllib.parse
from typing import Type

from creart import AbstractCreator, CreateTargetInfo, exists_module
from loguru import logger
from rich.console import Console
from rich.text import Text

# Console used for log output *outside* the full-screen TUI (legacy REPL and
# headless runs).  Inside the TUI every line is captured by ``src.tui.log_sink``
# instead, so this path is never used there.
#
# ``soft_wrap`` keeps the terminal's own wrapping and ``markup=False`` stops
# Rich from interpreting square brackets; loguru already emits ANSI colour
# codes, which ``Text.from_ansi`` turns into styled text.
_console = Console(highlight=False, soft_wrap=True, markup=False)


def _safe_print(msg):
    """Print a formatted log line; fall back to stderr when there is no
    interactive console (headless / CI runs)."""
    try:
        _console.print(Text.from_ansi(msg), end="")
    except Exception:
        sys.__stderr__.write(msg)
        sys.__stderr__.flush()


def _format(record):
    extra = record.get("extra", {})
    item_type = extra.get("item_type")
    tag = extra.get("tag")
    if item_type and tag:
        return ("<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green>"
                + f" | <b>{item_type}</b> | <b>{tag}</b>"
                + " | <level>{level}</level> - <level>{message}\n</level>")
    return ("<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green>"
            + " | <level>{level}</level> - <level>{message}\n</level>")


class GlobalLogger:
    def __init__(self):
        logger.remove()
        self.logger = logger
        logger.add(lambda msg: _safe_print(msg), colorize=True,
                   format=_format, level="INFO")


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
        self.item_id = urllib.parse.quote(item_id)
        # What is being ripped, for the per-item messages below.
        self.noun = "Music video" if _type == "music-video" else "Song"
        self.full_name = self.item_id
        self.logger = logger.bind(item_type=self.item_type.upper(), tag=self.item_id)

    def create(self):
        self.logger.info(f"Start ripping...")

    def set_fullname(self, artist: str, name: str = None):
        if not name:
            self.full_name = artist
        else:
            self.full_name = f"{artist} - {name}"
        clean_name = self.full_name.replace("<", "\\<").replace(">", "\\>")
        self.logger = logger.bind(item_type=self.item_type.upper(), tag=clean_name)

    def not_exist(self):
        self.logger.error(
            f"Unable to download {self.item_type}. This {self.item_type} does not exist in all available storefronts")

    def language_not_exist(self, region: str, current_language: str, default_language: str):
        self.logger.warning(
            f"Selected language {current_language} does not exist in region {region.upper()}, falling back to {default_language}")

    def already_exist(self):
        self.logger.info(f"{self.noun} already exists")

    def lyrics_not_exist(self):
        self.logger.warning("Lyrics do not exist")

    def audio_not_exist(self):
        self.logger.error(f"Failed to download {self.noun.lower()}. Audio does not exist")

    def lossless_audio_not_exist(self):
        self.logger.error("Failed to download song. Lossless audio does not exist")

    def lossless_audio_not_exist_aac(self):
        self.logger.warning("Lossless audio does not exist. Using aac-legacy to rip")

    def downloading(self, detail: str = ""):
        self.logger.info(f"Downloading {self.noun.lower()}{f' ({detail})' if detail else ''}...")

    def decrypting(self):
        self.logger.info(f"Decrypting {self.noun.lower()}...")

    def failed_integrity(self, error_Level: bool):
        if error_Level:
            self.logger.error(f"{self.noun} did not pass the integrity check!")
        else:
            self.logger.warning(f"{self.noun} did not pass the integrity check!")

    def saved(self):
        self.logger.success(f"{self.noun} saved!")

    def done(self):
        self.logger.success(f"Finished ripping")

    def selected_codec(self, selected_codec):
        self.logger.info(f"Selected codec: {selected_codec}")

    def codec_alternative(self):
        self.logger.warning("Unable to find the specified audio, switched to another audio codec")
