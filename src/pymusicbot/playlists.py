"""Playlist files in the Playlists folder, in JMusicBot's format.

Each playlist is ``<name>.txt`` with one entry per line: a URL, a file or folder from the music
library, or words to search YouTube for. Blank lines are skipped, and lines starting with ``#``
or ``//`` are comments.

A marker line sets how the playlist is shuffled:
- ``#shuffle`` (JMusicBot's): every song plays once, in random order, before any repeats.
- ``#random``: each song is picked at random from all of them, so repeats can come at any time.
``//`` works in place of ``#`` for both.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

_UNSAFE = re.compile(r'[*?|\\/":<>]')


class PlaylistError(Exception):
    """A playlist action failed; the message is shown to the user."""


class ShuffleMode(StrEnum):
    OFF = "off"  # file order
    SHUFFLE = "shuffle"  # every song once in random order, then again in a new order
    RANDOM = "random"  # each song picked at random from all of them


_MARKERS = {f"{prefix}{mode.value}": mode for prefix in ("#", "//") for mode in (ShuffleMode.SHUFFLE, ShuffleMode.RANDOM)}


@dataclass
class Playlist:
    name: str
    items: list[str]
    mode: ShuffleMode = ShuffleMode.OFF


def clean_name(name: str) -> str:
    """JMusicBot's naming rules: spaces become underscores; path and wildcard characters are dropped."""
    return _UNSAFE.sub("", re.sub(r"\s+", "_", name.strip()))


class PlaylistStore:
    def __init__(self, folder: Path):
        self.folder = folder

    def names(self) -> list[str]:
        if not self.folder.is_dir():
            return []
        return sorted((p.stem for p in self.folder.glob("*.txt")), key=str.lower)

    def load(self, name: str, rng: random.Random | None = None) -> Playlist | None:
        path = self._path(name)
        if path is None or not path.is_file():
            return None
        items, mode = [], ShuffleMode.OFF
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith(("#", "//")):
                mode = _MARKERS.get(re.sub(r"\s+", "", line).lower(), mode)
                continue
            items.append(line)
        if mode == ShuffleMode.SHUFFLE:
            (rng or random).shuffle(items)
        return Playlist(path.stem, items, mode)

    def set_mode(self, name: str, mode: ShuffleMode) -> str:
        """Replace the shuffle marker, keeping every other line. The marker goes first."""
        path = self._existing(name)
        lines = [
            line for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if re.sub(r"\s+", "", line).lower() not in _MARKERS
        ]
        if mode != ShuffleMode.OFF:
            lines.insert(0, f"#{mode.value}")
        path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
        return path.stem

    def create(self, name: str) -> str:
        path = self._path(name)
        if path is None:
            raise PlaylistError("Please provide a name for the playlist!")
        if path.exists():
            raise PlaylistError(f"Playlist `{path.stem}` already exists!")
        self.folder.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path.stem

    def delete(self, name: str) -> str:
        path = self._existing(name)
        path.unlink()
        return path.stem

    def append(self, name: str, items: list[str]) -> str:
        """Add entries to the end, keeping the file's existing lines and comments."""
        path = self._existing(name)
        text = path.read_text(encoding="utf-8", errors="replace")
        if text and not text.endswith("\n"):
            text += "\n"
        path.write_text(text + "".join(f"{item}\n" for item in items), encoding="utf-8")
        return path.stem

    def remove(self, name: str, position: int) -> tuple[str, str]:
        """Remove the entry at 1-based `position` (comments don't count). Returns (playlist, entry)."""
        path = self._existing(name)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        entry_lines = [i for i, line in enumerate(lines) if line.strip() and not line.strip().startswith(("#", "//"))]
        if not 1 <= position <= len(entry_lines):
            raise PlaylistError(f"Position must be between 1 and {len(entry_lines)}!")
        removed = lines.pop(entry_lines[position - 1]).strip()
        path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
        return path.stem, removed

    def entries(self, name: str) -> list[str]:
        """The entries in file order (unshuffled), for showing and picking."""
        path = self._path(name)
        if path is None or not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return [line.strip() for line in lines if line.strip() and not line.strip().startswith(("#", "//"))]

    def _existing(self, name: str) -> Path:
        path = self._path(name)
        if path is None or not path.is_file():
            raise PlaylistError(f"Playlist `{clean_name(name)}` doesn't exist!")
        return path

    def _path(self, name: str) -> Path | None:
        name = clean_name(name)
        if not name or name in (".", ".."):
            return None
        return self.folder / f"{name}.txt"
