"""Bot configuration.

Two sources, by who owns them:
- Secrets come from environment variables (``.env`` under Docker): ``DISCORD_TOKEN``, and
  optionally ``OWNER_ID``.
- Everything else comes from ``config.toml``, which the admin edits and the bot only reads.
  Every key is optional.

Where things live: PYMUSICBOT_CONFIG (the config file), PYMUSICBOT_DATA (settings and playlists)
and PYMUSICBOT_MUSIC (the default music folder). Without them the bot uses config.toml, data/ and
music/ in the working directory; the Docker image sets them to /config, /data and /music.

Relative paths in ``config.toml`` are resolved against the data directory.
"""

from __future__ import annotations

import difflib
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = "config.toml"
DEFAULT_DATA_DIR = "data"
DEFAULT_MUSIC_DIR = "music"

STATUSES = ("online", "idle", "dnd", "invisible")
LOG_LEVELS = ("debug", "info", "warning", "error", "critical")


class ConfigError(Exception):
    """The configuration is missing something or has an invalid value."""


@dataclass(frozen=True)
class PresenceConfig:
    game: str = "DEFAULT"
    status: str = "online"
    song_in_status: bool = False


@dataclass(frozen=True)
class PlayerConfig:
    stay_in_channel: bool = False
    max_track_length: int = 0  # seconds; 0 = no limit
    max_playlist_tracks: int = 1000
    skip_ratio: float = 0.55
    alone_time_until_stop: int = 0  # seconds; 0 = never leave
    now_playing_images: bool = False


@dataclass(frozen=True)
class FilesConfig:
    music_folders: tuple[Path, ...] = (Path(DEFAULT_MUSIC_DIR),)
    playlists_folder: Path = Path("Playlists")


@dataclass(frozen=True)
class EmojiConfig:
    success: str = "🎶"
    warning: str = "💡"
    error: str = "🚫"
    loading: str = "⌚"
    searching: str = "🔎"


@dataclass(frozen=True)
class Config:
    token: str = field(repr=False)
    owner_id: int | None
    data_dir: Path
    log_level: str = "info"
    presence: PresenceConfig = PresenceConfig()
    player: PlayerConfig = PlayerConfig()
    files: FilesConfig = FilesConfig()
    emoji: EmojiConfig = EmojiConfig()


# Allowed keys and their TOML types, per table. `list` means a list of strings.
_TOP_LEVEL = {"owner_id": int, "log_level": str}
_SECTIONS: dict[str, dict[str, type]] = {
    "presence": {"game": str, "status": str, "song_in_status": bool},
    "player": {
        "stay_in_channel": bool,
        "max_track_length": int,
        "max_playlist_tracks": int,
        "skip_ratio": float,
        "alone_time_until_stop": int,
        "now_playing_images": bool,
    },
    "files": {"music_folders": list, "playlists_folder": str},
    "emoji": {"success": str, "warning": str, "error": str, "loading": str, "searching": str},
}
_TYPE_NAMES = {bool: "true or false", int: "a whole number", float: "a number", str: "a string", list: "a list of strings"}


def load_config(environ: dict[str, str] | None = None) -> Config:
    """Load config.toml and the environment. Raises ConfigError with a readable message."""
    env = os.environ if environ is None else environ
    config_path = Path(env.get("PYMUSICBOT_CONFIG", DEFAULT_CONFIG_PATH))
    data_dir = Path(env.get("PYMUSICBOT_DATA", DEFAULT_DATA_DIR))

    token = env.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise ConfigError("DISCORD_TOKEN is not set. Put it in the .env file.")

    raw = _read_toml(config_path)
    known = set(_TOP_LEVEL) | set(_SECTIONS)
    _reject_unknown(raw, known, "")

    top = _take(raw, _TOP_LEVEL, "")
    sections = {}
    for name, spec in _SECTIONS.items():
        table = raw.get(name, {})
        if not isinstance(table, dict):
            raise ConfigError(f"[{name}] must be a table (a section starting with [{name}]).")
        _reject_unknown(table, set(spec), f"{name}.")
        sections[name] = _take(table, spec, f"{name}.")

    owner_id = top.get("owner_id")
    if env.get("OWNER_ID", "").strip():
        try:
            owner_id = int(env["OWNER_ID"])
        except ValueError:
            raise ConfigError(f"OWNER_ID must be a Discord user ID (a number), got {env['OWNER_ID']!r}.") from None
    if owner_id is not None and owner_id <= 0:
        raise ConfigError("owner_id must be a Discord user ID. Remove it to use the application owner.")

    log_level = top.get("log_level", "info").lower()
    if log_level not in LOG_LEVELS:
        raise ConfigError(f"log_level must be one of {', '.join(LOG_LEVELS)}, got {log_level!r}.")

    presence_raw = sections["presence"]
    if "status" in presence_raw:
        presence_raw["status"] = presence_raw["status"].lower()
    presence = PresenceConfig(**presence_raw)
    if presence.status not in STATUSES:
        raise ConfigError(f"presence.status must be one of {', '.join(STATUSES)}, got {presence.status!r}.")

    player = PlayerConfig(**sections["player"])
    if not 0 <= player.skip_ratio <= 1:
        raise ConfigError(f"player.skip_ratio must be between 0 and 1, got {player.skip_ratio}.")
    for key in ("max_track_length", "max_playlist_tracks", "alone_time_until_stop"):
        if getattr(player, key) < 0:
            raise ConfigError(f"player.{key} can't be negative.")

    files_raw = sections["files"]
    if "music_folders" in files_raw:
        music_folders = tuple(data_dir / p for p in files_raw["music_folders"])
    else:
        music_folders = (Path(env.get("PYMUSICBOT_MUSIC", DEFAULT_MUSIC_DIR)),)
    files = FilesConfig(
        music_folders=music_folders,
        playlists_folder=data_dir / files_raw.get("playlists_folder", "Playlists"),
    )

    return Config(
        token=token,
        owner_id=owner_id,
        data_dir=data_dir,
        log_level=log_level,
        presence=presence,
        player=player,
        files=files,
        emoji=EmojiConfig(**sections["emoji"]),
    )


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}  # every key has a default
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} is not valid TOML: {e}") from None


def _reject_unknown(table: dict[str, Any], known: set[str], prefix: str) -> None:
    for key in table:
        if key not in known:
            hint = difflib.get_close_matches(key, known, n=1)
            suggestion = f" Did you mean {prefix}{hint[0]}?" if hint else ""
            raise ConfigError(f"Unknown setting {prefix}{key}.{suggestion}")


def _take(table: dict[str, Any], spec: dict[str, type], prefix: str) -> dict[str, Any]:
    values = {}
    for key, kind in spec.items():
        if key not in table:
            continue
        value = table[key]
        if kind is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        valid = (
            all(isinstance(v, str) for v in value) if kind is list and isinstance(value, list)
            else isinstance(value, kind) and not (kind is int and isinstance(value, bool))
        )
        if not valid:
            raise ConfigError(f"{prefix}{key} must be {_TYPE_NAMES[kind]}, got {value!r}.")
        values[key] = value
    return values
