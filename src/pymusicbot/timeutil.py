"""Formatting and parsing of track times, ported from JMusicBot's TimeUtil."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

_NUMBER = re.compile(r"\d+(?:\.\d*)?|\.\d+")


@dataclass(frozen=True)
class SeekTime:
    milliseconds: int
    relative: bool  # True for "+1:30" / "-10": move from the current position


def format_time(seconds: float | None) -> str:
    """1:02:03 or 02:03; LIVE for streams without a duration."""
    if seconds is None:
        return "LIVE"
    total = _round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return (f"{hours}:" if hours else "") + f"{minutes:02}:{secs:02}"


def parse_seek(text: str) -> SeekTime | None:
    """Parse [+|-] HH:MM:SS, MM:SS, SS, or unit time like 1h20m5s. None if it can't be parsed."""
    if not text:
        return None
    relative = text[0] in "+-"
    backwards = text[0] == "-"
    timestamp = text[1:] if relative else text

    milliseconds = parse_colon_time(timestamp)
    if milliseconds == -1:
        milliseconds = parse_unit_time(timestamp)
    if milliseconds == -1:
        return None
    return SeekTime(-milliseconds if backwards else milliseconds, relative)


def parse_colon_time(timestamp: str) -> int:
    """HH:MM:SS, MM:SS or SS (decimals allowed) in milliseconds, or -1."""
    parts = re.split(r":+", timestamp)
    if len(parts) > 3:
        return -1
    units = [0.0, 0.0, 0.0]  # hours, minutes, seconds
    for index, part in enumerate(parts):
        part = part.replace(",", ".")
        if not _NUMBER.fullmatch(part):
            return -1
        units[index + 3 - len(parts)] = float(part)
    return _round(units[0] * 3_600_000 + units[1] * 60_000 + units[2] * 1000)


def parse_unit_time(text: str) -> int:
    """Unit time like 20m10, 1d5h20m14s or '1h and 20m' in milliseconds, or -1."""
    text = re.sub(r"(?i)(\s|,|and)", "", text)
    text = re.sub(r"(?is)(-?\d+|[a-z]+)", r"\1 ", text).strip()
    values = text.split()
    total = 0
    try:
        for i in range(0, len(values), 2):
            number = int(values[i])
            if i + 1 < len(values):
                unit = values[i + 1].lower()
                if unit.startswith("m"):
                    number *= 60
                elif unit.startswith("h"):
                    number *= 3600
                elif unit.startswith("d"):
                    number *= 86400
            total += number * 1000
    except ValueError:
        return -1
    return total


def _round(value: float) -> int:
    """Round half up, like Java's Math.round (Python's round() rounds half to even)."""
    return math.floor(value + 0.5)
