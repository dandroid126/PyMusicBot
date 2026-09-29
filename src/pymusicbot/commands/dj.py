"""Player controls for DJs: admins and members with the server's DJ role."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import MusicBot
from ..checks import Denied, dj_only
from ..formatting import title, volume_icon
from ..settings import RepeatMode
from .music import music_checks


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
