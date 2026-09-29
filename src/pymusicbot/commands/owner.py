"""Commands for the bot owner only."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import MusicBot
from ..checks import owner_only

log = logging.getLogger(__name__)


class Owner(commands.Cog):
    def __init__(self, bot: MusicBot):
        self.bot = bot

    @app_commands.command(description="Shut the bot down (owner only)")
    @owner_only
    async def shutdown(self, interaction: discord.Interaction[MusicBot]) -> None:
        log.info("Shutdown requested by %s", interaction.user)
        await interaction.response.send_message(self.bot.reply("warning", "Shutting down..."), ephemeral=True)
        await self.bot.close()


async def setup(bot: MusicBot) -> None:
    await bot.add_cog(Owner(bot))
