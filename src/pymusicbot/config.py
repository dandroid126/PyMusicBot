"""Bot configuration.

Two sources, by who owns them:
- Secrets come from environment variables: ``DISCORD_TOKEN``, and optionally ``OWNER_ID`` (one
  or more IDs, separated by commas).
  They're usually kept in ``.env``, which Docker Compose reads, and which the bot reads itself
  when it runs without Docker (see ``load_env_file``).
- Everything else comes from ``config.toml``, which the admin edits and the bot only reads.
  Every key is optional.

Where things live: PYMUSICBOT_CONFIG (the config file), PYMUSICBOT_DATA (settings and playlists)
and PYMUSICBOT_MUSIC (the default music folder, else MUSIC_DIR from .env). Without them the bot
uses config/config.toml, data/ and music/ in the working directory; the Docker image sets them to /config, /data and /music.

Relative paths in ``config.toml`` are resolved against the data directory.
"""

from __future__ import annotations

import difflib
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = "config/config.toml"
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


# Sites links can be played from; a site's subdomains count too. Each needs its own yt-dlp
# extractor: links to any other page are refused, so users can't make the bot fetch arbitrary
# addresses, such as ones on the server's own network.
DEFAULT_ALLOWED_SITES = ("youtube.com", "youtu.be", "soundcloud.com", "bandcamp.com")


@dataclass(frozen=True)
class SourcesConfig:
    allowed_sites: tuple[str, ...] = DEFAULT_ALLOWED_SITES


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
    owner_ids: tuple[int, ...]  # empty: the Discord application's owner
    data_dir: Path
    log_level: str = "info"
    presence: PresenceConfig = PresenceConfig()
    player: PlayerConfig = PlayerConfig()
    files: FilesConfig = FilesConfig()
    sources: SourcesConfig = SourcesConfig()
    emoji: EmojiConfig = EmojiConfig()


# Allowed keys and their TOML types, per table. `list` means a list of strings, `tuple` a whole
# number or a list of them. owner_ids is another name for owner_id.
_TOP_LEVEL = {"owner_id": tuple, "owner_ids": tuple, "log_level": str}
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
    "sources": {"allowed_sites": list},
    "emoji": {"success": str, "warning": str, "error": str, "loading": str, "searching": str},
}
_TYPE_NAMES = {
    bool: "true or false",
    int: "a whole number",
    float: "a number",
    str: "a string",
    list: "a list of strings",
    tuple: "a Discord user ID or a list of them, e.g. 123 or [123, 456]",
}


def load_env_file(path: Path = Path(".env"), environ: dict[str, str] | None = None) -> None:
    """Put the NAME=value lines of a .env file into the environment, for running without Docker.

    Variables that are already set win, like with Docker Compose. Blank lines and # comments are
    skipped, and quotes around a value are removed. A missing file is fine.
    """
    env = os.environ if environ is None else environ
    try:
        # utf-8-sig: Notepad may start the file with a byte order mark.
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.removeprefix("export ").partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not name:
            raise ConfigError(f"{path} line {number} should look like NAME=value, got {line!r}.")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env.setdefault(name, value)


def load_config(environ: dict[str, str] | None = None) -> Config:
    """Load config.toml and the environment. Raises ConfigError with a readable message.

    DISCORD_TOKEN is removed from the environment once read, so the programs the bot starts
    (FFmpeg, and Deno running YouTube's JavaScript) don't inherit it.
    """
    env = os.environ if environ is None else environ
    config_path = Path(env.get("PYMUSICBOT_CONFIG", DEFAULT_CONFIG_PATH))
    data_dir = Path(env.get("PYMUSICBOT_DATA", DEFAULT_DATA_DIR))

    token = env.pop("DISCORD_TOKEN", "").strip()
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

    # owner_id or owner_ids (the same setting under two names): one ID or a list. OWNER_ID in .env
    # replaces it.
    if "owner_id" in top and "owner_ids" in top:
        raise ConfigError("Set owner_id or owner_ids, not both. They're the same setting: one Discord user ID or a list.")
    owners = top.get("owner_id", top.get("owner_ids", []))
    owner_ids = owners if isinstance(owners, list) else [owners]
    if env.get("OWNER_ID", "").strip():
        try:
            owner_ids = [int(part) for part in env["OWNER_ID"].split(",") if part.strip()]
        except ValueError:
            raise ConfigError(
                f"OWNER_ID must be Discord user IDs (numbers, separated by commas), got {env['OWNER_ID']!r}."
            ) from None
    if any(owner <= 0 for owner in owner_ids):
        raise ConfigError("owner_id must be Discord user IDs. Remove it to use the application owner.")

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
        # MUSIC_DIR is the host folder in .env. Docker mounts it at /music and sets
        # PYMUSICBOT_MUSIC to that; without Docker the bot uses MUSIC_DIR itself.
        music = env.get("PYMUSICBOT_MUSIC") or env.get("MUSIC_DIR", "").strip() or DEFAULT_MUSIC_DIR
        music_folders = (Path(music),)
    files = FilesConfig(
        music_folders=music_folders,
        playlists_folder=data_dir / files_raw.get("playlists_folder", "Playlists"),
    )

    return Config(
        token=token,
        owner_ids=tuple(dict.fromkeys(owner_ids)),  # without duplicates, in order
        data_dir=data_dir,
        log_level=log_level,
        presence=presence,
        player=player,
        files=files,
        sources=_sources(sections["sources"]),
        emoji=EmojiConfig(**sections["emoji"]),
    )


def _sources(raw: dict[str, Any]) -> SourcesConfig:
    if "allowed_sites" not in raw:
        return SourcesConfig()
    sites = tuple(site.strip().lower().removeprefix("www.") for site in raw["allowed_sites"])
    for site in sites:
        if not site or any(c in site for c in "/:@ "):
            raise ConfigError(f"sources.allowed_sites takes site names like \"youtube.com\", got {site!r}.")
    return SourcesConfig(allowed_sites=sites)


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
        if kind is list:
            valid = isinstance(value, list) and all(isinstance(v, str) for v in value)
        elif kind is tuple:
            items = value if isinstance(value, list) else [value]
            valid = all(isinstance(v, int) and not isinstance(v, bool) for v in items)
        else:
            valid = isinstance(value, kind) and not (kind is int and isinstance(value, bool))
        if not valid:
            raise ConfigError(f"{prefix}{key} must be {_TYPE_NAMES[kind]}, got {value!r}.")
        values[key] = value
    return values
