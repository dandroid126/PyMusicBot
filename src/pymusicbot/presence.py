"""The bot's status and activity ("Playing X", "Listening to X", ...) from config."""

from __future__ import annotations

import discord

STATUSES = {
    "online": discord.Status.online,
    "idle": discord.Status.idle,
    "dnd": discord.Status.dnd,
    "invisible": discord.Status.invisible,
}


def parse_activity(game: str) -> discord.BaseActivity | None:
    """Parse JMusicBot's `game` setting.

    DEFAULT shows "Listening to /play", NONE shows nothing, and otherwise the text may start
    with Playing, Listening (to), Watching or Streaming <twitch user>. Without one of those
    it's "Playing <text>".
    """
    text = game.strip()
    lower = text.lower()
    if lower in ("", "none"):
        return None
    if lower == "default":
        return discord.Activity(type=discord.ActivityType.listening, name="/play")
    for prefix, kind in (
        ("listening to", discord.ActivityType.listening),
        ("listening", discord.ActivityType.listening),
        ("watching", discord.ActivityType.watching),
        ("playing", discord.ActivityType.playing),
    ):
        if lower.startswith(prefix):
            return discord.Activity(type=kind, name=text[len(prefix):].strip() or "​")
    if lower.startswith("streaming"):
        parts = text[len("streaming"):].split(maxsplit=1)
        if len(parts) == 2:
            return discord.Streaming(name=parts[1], url=f"https://twitch.tv/{parts[0]}")
    return discord.Game(name=text)
