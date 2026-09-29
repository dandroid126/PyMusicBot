"""Message formatting shared by commands, ported from JMusicBot's FormatUtil."""

from __future__ import annotations

from discord.utils import escape_markdown

from .audio.track import Track
from .timeutil import format_time

PLAY_EMOJI = "▶"
PAUSE_EMOJI = "⏸"
STOP_EMOJI = "⏹"


def progress_bar(fraction: float) -> str:
    """12 segments with a knob at the current position; a negative fraction shows no knob."""
    knob = int(fraction * 12) if fraction >= 0 else -1
    return "".join("🔘" if i == knob else "▬" for i in range(12))


def volume_icon(volume: int) -> str:
    if volume == 0:
        return "🔇"
    if volume < 30:
        return "🔈"
    if volume < 70:
        return "🔉"
    return "🔊"


def title(track: Track) -> str:
    """The track's title in bold, escaped so titles can't break the formatting."""
    return f"**{escape_markdown(track.title)}**"


def linked_title(track: Track) -> str:
    """The title in bold, linked to the track's page when it has one."""
    if track.url and track.url.startswith("http"):
        text = escape_markdown(track.title).replace("[", "(").replace("]", ")")
        return f"[**{text}**](<{track.url}>)"
    return title(track)


def queue_line(track: Track) -> str:
    requester = f" - <@{track.requester_id}>" if track.requester else ""
    return f"`[{format_time(track.duration)}]` {linked_title(track)}{requester}"
