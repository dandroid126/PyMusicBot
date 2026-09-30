"""Player controls for DJs: admins and members with the server's DJ role."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..audio.sources import SourceError
from ..bot import MusicBot
from ..checks import Denied, dj_only
from ..formatting import title, volume_icon
from ..settings import RepeatMode
from ..timeutil import format_time
from .music import library_choices, music_checks, requester_of


def dj_command(func):
    return app_commands.guild_only()(dj_only(func))


class DJ(commands.Cog):
    def __init__(self, bot: MusicBot):
        self.bot = bot

    @app_commands.command(description="Pause the current song (DJ)")
    @dj_command
    async def pause(self, interaction: discord.Interaction[MusicBot]) -> None:
        player, _ = music_checks(interaction, playing=True)
        if player.paused:
            raise Denied("The player is already paused! Use `/play` to unpause!")
        player.pause()
        await interaction.response.send_message(
            self.bot.reply("success", f"Paused {title(player.current)}. Use `/play` to unpause!")
        )

    @app_commands.command(description="Skip the current song without a vote (DJ)")
    @dj_command
    async def forceskip(self, interaction: discord.Interaction[MusicBot]) -> None:
        player, _ = music_checks(interaction, playing=True)
        track = player.current
        by = f"(requested by **{track.requester.name}**)" if track.requester else "(autoplay)"
        player.skip()
        await interaction.response.send_message(self.bot.reply("success", f"Skipped {title(track)} {by}"))

    @app_commands.command(description="Skip ahead to a position in the queue (DJ)")
    @dj_command
    async def skipto(self, interaction: discord.Interaction[MusicBot], position: app_commands.Range[int, 1]) -> None:
        player, _ = music_checks(interaction, playing=True)
        if position > len(player.queue):
            raise Denied(f"Position must be a whole number between 1 and {len(player.queue)}!")
        player.queue.skip(position - 1)
        next_track = player.queue[0]
        player.skip()
        await interaction.response.send_message(self.bot.reply("success", f"Skipped to {title(next_track)}"))

    @app_commands.command(description="Remove every song a member added (DJ)")
    @dj_command
    async def forceremove(self, interaction: discord.Interaction[MusicBot], member: discord.Member) -> None:
        player, _ = music_checks(interaction, playing=True)
        if not player.queue:
            raise Denied("There is nothing in the queue!")
        count = player.queue.remove_all(member.id)
        if count:
            await interaction.response.send_message(self.bot.reply("success", f"Removed `{count}` entries from {member.mention}."))
        else:
            await interaction.response.send_message(self.bot.reply("warning", f"{member.mention} doesn't have any songs in the queue!"))

    @app_commands.command(description="Move a song to a different position in the queue (DJ)")
    @app_commands.rename(source="from", destination="to")
    @dj_command
    async def movetrack(
        self, interaction: discord.Interaction[MusicBot], source: app_commands.Range[int, 1], destination: app_commands.Range[int, 1]
    ) -> None:
        player, _ = music_checks(interaction, playing=True)
        if source == destination:
            raise Denied("Can't move a track to the same position.")
        for position in (source, destination):
            if position > len(player.queue):
                raise Denied(f"`{position}` is not a valid position in the queue!")
        track = player.queue.move(source - 1, destination - 1)
        await interaction.response.send_message(
            self.bot.reply("success", f"Moved {title(track)} from position `{source}` to `{destination}`.")
        )

    @app_commands.command(description="Put a song at the front of the queue (DJ)")
    @app_commands.describe(query="A URL, a file from the music library, or words to search YouTube for")
    @dj_command
    async def playnext(self, interaction: discord.Interaction[MusicBot], query: str) -> None:
        player, channel = music_checks(interaction, joining=True)
        await interaction.response.defer(thinking=True)
        try:
            resolved = await self.bot.sources.resolve(query, requester_of(interaction.user))
        except SourceError as e:
            await interaction.followup.send(self.bot.reply("error", f"Couldn't load that: {discord.utils.escape_markdown(str(e))}"))
            return
        if not resolved.tracks:
            track = resolved.too_long[0]
            limit = format_time(self.bot.config.player.max_track_length)
            await interaction.followup.send(self.bot.reply(
                "warning", f"This track ({title(track)}) is longer than the allowed maximum: `{format_time(track.duration)}` > `{limit}`"
            ))
            return
        try:
            await player.connect(channel)
        except (discord.ClientException, TimeoutError):
            await interaction.followup.send(self.bot.reply("error", f"I couldn't connect to {channel.mention}."))
            return
        track = resolved.tracks[0]  # like JMusicBot, only the first track of a playlist or folder
        position = await player.add(track, front=True)
        where = "to begin playing" if position == -1 else "to the front of the queue"
        await interaction.followup.send(self.bot.reply("success", f"Added {title(track)} (`{format_time(track.duration)}`) {where}"))

    @playnext.autocomplete("query")
    async def playnext_autocomplete(self, interaction: discord.Interaction[MusicBot], current: str) -> list[app_commands.Choice[str]]:
        return await library_choices(self.bot, current)

    @app_commands.command(description="Stop playing, clear the queue and leave the voice channel (DJ)")
    @dj_command
    async def stop(self, interaction: discord.Interaction[MusicBot]) -> None:
        player, _ = music_checks(interaction)
        await player.stop()
        await interaction.response.send_message(self.bot.reply("success", "The player has stopped and the queue has been cleared."))

    @app_commands.command(description="Show or set the volume (DJ)")
    @app_commands.describe(level="0 to 150; 100 is normal")
    @dj_command
    async def volume(self, interaction: discord.Interaction[MusicBot], level: app_commands.Range[int, 0, 150] | None = None) -> None:
        player, _ = music_checks(interaction)
        if level is None:
            await interaction.response.send_message(f"{volume_icon(player.volume)} Current volume is `{player.volume}`")
            return
        old = player.volume
        player.set_volume(level)
        self.bot.settings.update(interaction.guild_id, volume=level)
        await interaction.response.send_message(f"{volume_icon(level)} Volume changed from `{old}` to `{level}`")

    @app_commands.command(description="Re-add songs to the queue when they finish (DJ)")
    @app_commands.describe(mode="Leave empty to toggle between off and all")
    @dj_command
    async def repeat(self, interaction: discord.Interaction[MusicBot], mode: RepeatMode | None = None) -> None:
        # Like JMusicBot, this works in any text channel.
        current = self.bot.settings.get(interaction.guild_id).repeat_mode
        if mode is None:
            mode = RepeatMode.ALL if current == RepeatMode.OFF else RepeatMode.OFF
        self.bot.settings.update(interaction.guild_id, repeat_mode=mode)
        await interaction.response.send_message(self.bot.reply("success", f"Repeat mode is now `{mode.value.title()}`"))


async def setup(bot: MusicBot) -> None:
    await bot.add_cog(DJ(bot))
