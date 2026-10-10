"""Optional health reports to a push monitor such as Uptime Kuma's.

Only created when HEALTH_PUSH_URL is set; without it nothing here runs. Every minute the bot
calls the URL with status=up, or status=down and the reason. The monitor alerts on "down", and
also when the calls stop (the bot crashed, froze or lost its network).

Healthy means connected to Discord, and playing wherever the bot is expected to: where it was
playing and wasn't stopped on purpose, and in every server with both a /setvc channel and a
default playlist (unless stopped there on purpose). That's judged from the settings, not only
from the player's own state, so a bug that clears the player is still caught. Short gaps, like
loading the next song or reconnecting, are ignored for GRACE_SECONDS.
"""

from __future__ import annotations

import logging
import math
import time
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from discord.ext import tasks

if TYPE_CHECKING:
    import discord

    from .bot import MusicBot

log = logging.getLogger(__name__)

INTERVAL_SECONDS = 60
GRACE_SECONDS = 120


def push_base_url(url: str) -> str:
    """The URL without its query: Uptime Kuma shows push URLs with ?status=up&msg=OK&ping= attached."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class HealthPush:
    def __init__(self, bot: MusicBot, url: str):
        self.bot = bot
        self.url = push_base_url(url)
        self._session: aiohttp.ClientSession | None = None
        self._first_seen: dict[str, float] = {}  # problem -> when it was first noticed
        self._discord_down_since: float | None = None
        self._failing = False

    def start(self) -> None:
        self.bot.add_listener(self._on_disconnect, "on_disconnect")
        self.bot.add_listener(self._on_connected, "on_ready")
        self.bot.add_listener(self._on_connected, "on_resumed")
        self._loop.start()
        log.info("Reporting health every %ds to the push monitor", INTERVAL_SECONDS)

    async def stop(self) -> None:
        self._loop.cancel()
        if self._session is not None:
            await self._session.close()

    async def _on_disconnect(self) -> None:
        if self._discord_down_since is None:
            self._discord_down_since = time.monotonic()

    async def _on_connected(self) -> None:
        self._discord_down_since = None

    # What's wrong

    def problems(self) -> list[str]:
        """Problems that have lasted longer than the grace period."""
        now = time.monotonic()
        current = self._current_problems(now)  # problem -> when it started, if known
        self._first_seen = {p: min(self._first_seen.get(p, now), started) for p, started in current.items()}
        return [
            f"{problem} for {math.floor((now - since) / 60)} min"
            for problem, since in self._first_seen.items()
            if now - since >= GRACE_SECONDS
        ]

    def _current_problems(self, now: float) -> dict[str, float]:
        if self._discord_down_since is not None or not self.bot.is_ready() or self.bot.is_closed():
            since = self._discord_down_since
            return {"Not connected to Discord": now if since is None else since}
        return {
            f"Not playing in {guild.name}": now
            for guild in self.bot.guilds
            if self._should_play(guild) and not self._playing(guild)
        }

    def _should_play(self, guild: discord.Guild) -> bool:
        player = self.bot.players.find(guild.id)
        if player is not None and player.wanted_channel_id is not None:
            return True
        settings = self.bot.settings.get(guild.id)
        radio = settings.voice_channel_id is not None and settings.default_playlist is not None
        return radio and not (player is not None and player.stopped_on_purpose)

    def _playing(self, guild: discord.Guild) -> bool:
        player = self.bot.players.find(guild.id)
        voice = player.voice if player is not None else None
        return voice is not None and (voice.is_playing() or voice.is_paused())

    # Reporting

    @tasks.loop(seconds=INTERVAL_SECONDS)
    async def _loop(self) -> None:
        await self.push()

    async def push(self) -> None:
        problems = self.problems()
        params = {"status": "down" if problems else "up", "msg": "; ".join(problems)[:250] or "OK"}
        latency = self.bot.latency
        if math.isfinite(latency):
            params["ping"] = str(round(latency * 1000))
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        try:
            async with self._session.get(self.url, params=params) as response:
                response.raise_for_status()
        except (aiohttp.ClientError, TimeoutError) as e:
            # The URL holds the monitor's token, so it's never logged.
            if not self._failing:
                reason = f"HTTP {e.status}" if isinstance(e, aiohttp.ClientResponseError) else type(e).__name__
                log.warning("Couldn't reach the push monitor: %s", reason)
            self._failing = True
        else:
            if self._failing:
                log.info("Reaching the push monitor again")
            self._failing = False
