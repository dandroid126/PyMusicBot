"""Commands anyone can use that aren't about playback."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import MusicBot


class General(commands.Cog):
    def __init__(self, bot: MusicBot):
        self.bot = bot

    @app_commands.command(description="Show this server's bot settings")
    @app_commands.guild_only()
    async def settings(self, interaction: discord.Interaction[MusicBot]) -> None:
        guild = interaction.guild
        s = self.bot.settings.get(guild.id)

        def mention(obj: discord.abc.Snowflake | None, empty: str) -> str:
            return obj.mention if obj else empty

        dj_role = guild.get_role(s.dj_role_id) if s.dj_role_id else None
        skip_ratio = s.skip_ratio if s.skip_ratio is not None else self.bot.config.player.skip_ratio
        embed = discord.Embed(title=f"{self.bot.user.name} settings", color=guild.me.color)
        embed.add_field(name="Voice channel", value=mention(guild.get_channel(s.voice_channel_id or 0), "Any"))
        embed.add_field(name="DJ role", value=mention(dj_role, "None"))
        embed.add_field(name="Repeat", value=s.repeat_mode.value.title())
        embed.add_field(name="Queue type", value=s.queue_type.value.title())
        embed.add_field(name="Default playlist", value=s.default_playlist or "None")
        embed.add_field(name="Volume", value=f"{s.volume}%")
        embed.add_field(
            name="Skip ratio",
            value=f"{skip_ratio:.0%}" + ("" if s.skip_ratio is not None else " (default)"),
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: MusicBot) -> None:
    await bot.add_cog(General(bot))
