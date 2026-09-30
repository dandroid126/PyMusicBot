"""The now-playing message, kept up to date like JMusicBot's NowplayingHandler.

The last /nowplaying message in each server is edited every few seconds to move the progress
bar. With now_playing_images on it isn't refreshed, because each edit would reload the image.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import tasks

from ..formatting import PAUSE_EMOJI, PLAY_EMOJI, STOP_EMOJI, progress_bar, volume_icon
from ..timeutil import format_time

if TYPE_CHECKING:
    from ..bot import MusicBot
    from .player import GuildPlayer

log = logging.getLogger(__name__)

REFRESH_SECONDS = 5


def now_playing(bot: MusicBot, player: GuildPlayer) -> tuple[str, discord.Embed]:
    """The message content and embed for the current track, or for nothing playing."""
    guild = player.guild
    color = guild.me.color if guild else discord.Color.default()
    track, voice = player.current, player.voice
    if track is None or voice is None:
        embed = discord.Embed(
            title="No music playing",
            description=f"{STOP_EMOJI} {progress_bar(-1)} {volume_icon(player.volume)}",
            color=color,
        )
        return bot.reply("success", "**Now Playing...**"), embed

    embed = discord.Embed(title=track.title[:256], url=track.url if track.url and track.url.startswith("http") else None, color=color)
    if track.requester:
        embed.set_author(name=track.requester.name, icon_url=track.requester.avatar_url)
    if track.thumbnail and bot.config.player.now_playing_images:
        embed.set_thumbnail(url=track.thumbnail)
    if track.uploader:
        embed.set_footer(text=f"Source: {track.uploader}")
    elif track.local:
        embed.set_footer(text="Source: local file")
    position = player.position
    fraction = position / track.duration if track.duration else -1
    status = PAUSE_EMOJI if player.paused else PLAY_EMOJI
    embed.description = (
        f"{status} {progress_bar(fraction)} `[{format_time(position)}/{format_time(track.duration)}]` "
        f"{volume_icon(player.volume)}"
    )
    return bot.reply("success", f"**Now Playing in {voice.channel.mention}...**"), embed


class NowPlayingMessages:
    def __init__(self, bot: MusicBot):
        self.bot = bot
        self._messages: dict[int, discord.Message] = {}  # guild ID -> last /nowplaying message

    def start(self) -> None:
        if not self.bot.config.player.now_playing_images:
            self._refresh.start()

    def stop(self) -> None:
        self._refresh.cancel()

    def track(self, message: discord.Message) -> None:
        if message.guild:
            self._messages[message.guild.id] = message

    def forget(self, guild_id: int) -> None:
        self._messages.pop(guild_id, None)

    @tasks.loop(seconds=REFRESH_SECONDS)
    async def _refresh(self) -> None:
        for guild_id, message in list(self._messages.items()):
            player = self.bot.players.find(guild_id)
            if player is None:
                self.forget(guild_id)
                continue
            content, embed = now_playing(self.bot, player)
            if not player.is_active:
                self.forget(guild_id)  # one last edit to show nothing is playing
            try:
                await message.edit(content=content, embed=embed)
            except discord.HTTPException:
                self.forget(guild_id)

    @_refresh.error
    async def _refresh_error(self, error: BaseException) -> None:
        log.exception("Now-playing refresh failed", exc_info=error)
        self._refresh.restart()
