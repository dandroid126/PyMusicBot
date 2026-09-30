"""The local music library: the folders listed in config.toml's files.music_folders.

Only paths that resolve inside one of those folders can be played. resolve() follows symlinks
and collapses "..", so neither can be used to reach files outside them.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wav", ".wma", ".webm", ".mka", ".aiff", ".alac",
})
INDEX_MAX_AGE = 60  # seconds before autocomplete rescans the folders


class LocalLibrary:
    def __init__(self, folders: tuple[Path, ...]):
        self.folders = tuple(folder.resolve() for folder in folders)
        self._index: list[str] = []
        self._indexed_at = float("-inf")
        self._indexing: asyncio.Task | None = None

    def resolve(self, query: str) -> Path | None:
        """The file or folder `query` names inside a music folder, or None."""
        query = query.strip()
        if not query:
            return None
        for folder in self.folders:
            path = (folder / query).resolve()
            if not path.is_relative_to(folder):
                continue
            if path.is_dir() or (path.is_file() and is_audio(path)):
                return path
        return None

    def display_path(self, path: Path) -> str:
        """The path as users type it: relative to its music folder."""
        for folder in self.folders:
            if path.is_relative_to(folder):
                return path.relative_to(folder).as_posix()
        return path.name

    def audio_files(self, directory: Path) -> list[Path]:
        """Every audio file under `directory`, sorted, skipping links that lead outside the library."""
        files = []
        for path in sorted(directory.rglob("*")):
            real = path.resolve()
            if real.is_file() and is_audio(real) and any(real.is_relative_to(f) for f in self.folders):
                files.append(real)
        return files

    async def search(self, text: str, limit: int = 25) -> list[str]:
        """Library paths containing every word of `text`, folders first, for autocomplete."""
        entries = await self._entries()
        words = text.lower().split()
        return [entry for entry in entries if all(w in entry.lower() for w in words)][:limit]

    async def suggest(self, text: str) -> str | None:
        """The library path closest to `text`, ignoring case, spaces and punctuation."""
        target = _squash(text)
        if not target:
            return None
        keys: dict[str, str] = {}  # squashed path (files without extension) -> path
        for entry in await self._entries():  # folders come first, so they win ties
            keys.setdefault(_squash(entry if entry.endswith("/") else str(Path(entry).with_suffix(""))), entry)
        for key, entry in keys.items():
            if key.endswith(target):  # "DiamondDust" -> "Girls Band Cry/Diamond Dust/"
                return entry
        close = difflib.get_close_matches(target, list(keys), n=1, cutoff=0.6)
        return keys[close[0]] if close else None

    async def _entries(self) -> list[str]:
        if time.monotonic() - self._indexed_at > INDEX_MAX_AGE:
            if self._indexing is None or self._indexing.done():
                self._indexing = asyncio.create_task(asyncio.to_thread(self._scan))
            if not self._index:  # first use: wait for the scan instead of showing nothing
                await self._indexing
        return self._index

    def _scan(self) -> None:
        dirs, files = [], []
        for folder in self.folders:
            if not folder.is_dir():
                continue
            for path in folder.rglob("*"):
                relative = path.relative_to(folder).as_posix()
                if path.is_dir():
                    dirs.append(relative + "/")
                elif is_audio(path):
                    files.append(relative)
        self._index = sorted(dirs, key=str.lower) + sorted(files, key=str.lower)
        self._indexed_at = time.monotonic()
        log.debug("Indexed %d folders and %d files in the music library", len(dirs), len(files))


def _squash(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


async def probe(path: Path) -> tuple[str, float | None]:
    """The display title ("Artist - Title", or the file name) and duration in seconds."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path),
        stdout=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    try:
        fmt = json.loads(out or b"{}").get("format", {})
    except json.JSONDecodeError:
        fmt = {}
    tags = {key.lower(): value for key, value in fmt.get("tags", {}).items()}
    if tags.get("title"):
        title = f"{tags['artist']} - {tags['title']}" if tags.get("artist") else tags["title"]
    else:
        title = path.stem
    try:
        duration = float(fmt["duration"])
    except (KeyError, ValueError):
        duration = None
    return title, duration
