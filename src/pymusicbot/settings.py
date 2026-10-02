"""Per-server settings, owned and written by the bot.

Stored in ``<data dir>/settings.json``. On first start, if that file doesn't exist but
JMusicBot's ``serversettings.json`` does, the old file is converted into the new one. The old
file is never modified, and it's ignored once the new file exists.

JMusicBot's text channel setting (settc) isn't kept: which commands work where is set with
Discord's own command permissions instead. Saved files that still have it load fine, and it's
dropped on the next save.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SETTINGS_FILE = "settings.json"
LEGACY_SETTINGS_FILE = "serversettings.json"
FORMAT_VERSION = 1


class SettingsError(Exception):
    """A settings file couldn't be read. Nothing is overwritten when this is raised."""


class RepeatMode(StrEnum):
    OFF = "off"
    ALL = "all"
    SINGLE = "single"


class QueueType(StrEnum):
    LINEAR = "linear"
    FAIR = "fair"


@dataclass(frozen=True)
class GuildSettings:
    voice_channel_id: int | None = None
    dj_role_id: int | None = None
    volume: int = 100
    default_playlist: str | None = None
    repeat_mode: RepeatMode = RepeatMode.OFF
    skip_ratio: float | None = None  # None = use the config's player.skip_ratio
    queue_type: QueueType = QueueType.FAIR

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        # Discord IDs are stored as strings, like JMusicBot did, so tools that read JSON
        # numbers as floats don't corrupt them.
        for key in ("voice_channel_id", "dj_role_id"):
            if data[key] is not None:
                data[key] = str(data[key])
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> GuildSettings:
        return cls(
            voice_channel_id=_snowflake(data.get("voice_channel_id")),
            dj_role_id=_snowflake(data.get("dj_role_id")),
            volume=int(data.get("volume", 100)),
            default_playlist=data.get("default_playlist"),
            repeat_mode=RepeatMode(data.get("repeat_mode", RepeatMode.OFF)),
            skip_ratio=None if data.get("skip_ratio") is None else float(data["skip_ratio"]),
            queue_type=QueueType(data.get("queue_type", QueueType.FAIR)),
        )


class SettingsStore:
    def __init__(self, path: Path, guilds: dict[int, GuildSettings] | None = None):
        self.path = path
        self._guilds = guilds or {}

    @classmethod
    def load(cls, data_dir: Path) -> SettingsStore:
        path = data_dir / SETTINGS_FILE
        legacy = data_dir / LEGACY_SETTINGS_FILE
        if path.exists():
            store = cls(path, _parse(path))
            log.info("Loaded settings for %d server(s) from %s", len(store._guilds), path)
        elif legacy.exists():
            store = cls(path, convert_legacy(_read_json(legacy)))
            store.save()
            log.info(
                "Converted %s into %s (%d server(s)). The old file is left as it was and won't be read again.",
                legacy, path, len(store._guilds),
            )
        else:
            store = cls(path)
            log.info("No settings yet; %s will be created on the first change", path)
        return store

    def get(self, guild_id: int) -> GuildSettings:
        return self._guilds.get(guild_id, GuildSettings())

    def update(self, guild_id: int, **changes: Any) -> GuildSettings:
        settings = replace(self.get(guild_id), **changes)
        self._guilds[guild_id] = settings
        self.save()
        return settings

    def save(self) -> None:
        data = {
            "version": FORMAT_VERSION,
            "guilds": {str(gid): s.to_json() for gid, s in sorted(self._guilds.items())},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file and rename it into place, so a crash mid-write can't leave
        # a half-written settings file.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".settings-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def convert_legacy(data: Any) -> dict[int, GuildSettings]:
    """Convert JMusicBot's serversettings.json contents. Its per-server prefix is dropped."""
    if not isinstance(data, dict):
        raise SettingsError(f"{LEGACY_SETTINGS_FILE} should contain a JSON object")
    guilds = {}
    for guild_id, entry in data.items():
        if not isinstance(entry, dict):
            raise SettingsError(f"{LEGACY_SETTINGS_FILE}: server {guild_id} isn't a JSON object")
        try:
            repeat = entry.get("repeat_mode")
            if repeat is None and entry.get("repeat") is True:  # JMusicBot 0.3.3 and older
                repeat = "ALL"
            skip_ratio = entry.get("skip_ratio", -1)
            guilds[int(guild_id)] = GuildSettings(
                voice_channel_id=_snowflake(entry.get("voice_channel_id")),
                dj_role_id=_snowflake(entry.get("dj_role_id")),
                volume=int(entry.get("volume", 100)),
                default_playlist=entry.get("default_playlist"),
                repeat_mode=RepeatMode((repeat or "OFF").lower()),
                skip_ratio=None if float(skip_ratio) < 0 else float(skip_ratio),
                queue_type=QueueType(str(entry.get("queue_type", "FAIR")).lower()),
            )
        except (TypeError, ValueError) as e:
            raise SettingsError(f"{LEGACY_SETTINGS_FILE}: server {guild_id} has an invalid value: {e}") from None
    return guilds


def _parse(path: Path) -> dict[int, GuildSettings]:
    data = _read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("guilds"), dict):
        raise SettingsError(f"{path} doesn't look like a PyMusicBot settings file")
    if data.get("version") != FORMAT_VERSION:
        raise SettingsError(f"{path} has unsupported version {data.get('version')!r}")
    try:
        return {int(gid): GuildSettings.from_json(entry) for gid, entry in data["guilds"].items()}
    except (TypeError, ValueError, AttributeError) as e:
        raise SettingsError(f"{path} has an invalid value: {e}") from None


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SettingsError(f"Couldn't read {path}: {e}") from None


def _snowflake(value: Any) -> int | None:
    """A Discord ID from a string or number; JMusicBot used 0 or junk to mean 'not set'."""
    try:
        snowflake = int(value)
    except (TypeError, ValueError):
        return None
    return snowflake if snowflake > 0 else None
