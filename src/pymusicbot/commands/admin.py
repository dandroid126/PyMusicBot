"""Server settings commands, for the owner and members with Manage Server."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import MusicBot
from ..checks import admin_only
from ..settings import QueueType

_NO_PINGS = discord.AllowedMentions.none()


def admin_command(func):
    """Server-only, hidden from members without Manage Server, and checked when run."""
    return app_commands.guild_only()(app_commands.default_permissions(manage_guild=True)(admin_only(func)))


class Admin(commands.Cog):
    def __init__(self, bot: MusicBot):
        self.bot = bot

    @app_commands.command(description="Set the DJ role. Leave empty to remove it.")
    @admin_command
    @app_commands.describe(role="Members with this role can use DJ commands")
    async def setdj(self, interaction: discord.Interaction[MusicBot], role: discord.Role | None = None) -> None:
        self.bot.settings.update(interaction.guild_id, dj_role_id=role.id if role else None)
        if role:
            text = f"DJ commands can now be used by members with the {role.mention} role."
        else:
            text = "DJ role cleared; only admins can use DJ commands."
        await interaction.response.send_message(self.bot.reply("success", text), allowed_mentions=_NO_PINGS)

    @app_commands.command(description="Set the text channel for music commands. Leave empty to allow any.")
    @admin_command
    async def settc(self, interaction: discord.Interaction[MusicBot], channel: discord.TextChannel | None = None) -> None:
        self.bot.settings.update(interaction.guild_id, text_channel_id=channel.id if channel else None)
        text = f"Music commands can now only be used in {channel.mention}." if channel else "Music commands can now be used in any channel."
        await interaction.response.send_message(self.bot.reply("success", text))

    @app_commands.command(description="Set the voice channel for music. Leave empty to allow any.")
    @admin_command
    async def setvc(self, interaction: discord.Interaction[MusicBot], channel: discord.VoiceChannel | None = None) -> None:
        self.bot.settings.update(interaction.guild_id, voice_channel_id=channel.id if channel else None)
        text = f"Music can now only be played in {channel.mention}." if channel else "Music can now be played in any voice channel."
        await interaction.response.send_message(self.bot.reply("success", text))
        await self.bot.players.autostart(interaction.guild)

    @app_commands.command(description="Set the percentage of listeners needed to skip. Leave empty for the default.")
    @admin_command
    @app_commands.describe(percent="0 to 100")
    async def setskip(
        self, interaction: discord.Interaction[MusicBot], percent: app_commands.Range[int, 0, 100] | None = None
    ) -> None:
        self.bot.settings.update(interaction.guild_id, skip_ratio=None if percent is None else percent / 100)
        if percent is None:
            text = f"Skip ratio reset to the default ({self.bot.config.player.skip_ratio:.0%})."
        else:
            text = f"Listeners needed to skip a song: {percent}%."
        await interaction.response.send_message(self.bot.reply("success", text))

    @app_commands.command(description="Show or change how the queue orders songs")
    @admin_command
    @app_commands.rename(queue_type="type")
    @app_commands.describe(queue_type="Linear: first come, first served. Fair: takes turns between requesters.")
    async def queuetype(self, interaction: discord.Interaction[MusicBot], queue_type: QueueType | None = None) -> None:
        if queue_type is None:
            current = self.bot.settings.get(interaction.guild_id).queue_type
            await interaction.response.send_message(f"The queue type is **{current.value.title()}**.")
            return
        self.bot.settings.update(interaction.guild_id, queue_type=queue_type)
        if player := self.bot.players.find(interaction.guild_id):
            player.set_queue_type(queue_type)
        await interaction.response.send_message(self.bot.reply("success", f"Queue type set to **{queue_type.value.title()}**."))


async def setup(bot: MusicBot) -> None:
    await bot.add_cog(Admin(bot))
