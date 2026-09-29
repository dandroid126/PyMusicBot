"""The Discord client: loads command groups, syncs slash commands and reports errors."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from .config import Config
from .presence import STATUSES, parse_activity
from .settings import SettingsStore
from .startup import run_startup_checks

log = logging.getLogger(__name__)

EXTENSIONS = (
    "pymusicbot.commands.general",
    "pymusicbot.commands.admin",
    "pymusicbot.commands.owner",
)


class MusicBot(commands.Bot):
    def __init__(self, config: Config, settings: SettingsStore):
        # Default intents cover servers and voice states. Slash commands don't need the
        # privileged message content intent.
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=discord.Intents.default(),
            help_command=None,
            owner_id=config.owner_id,
            status=STATUSES[config.presence.status],
            activity=parse_activity(config.presence.game),
        )
        self.config = config
        self.settings = settings
        self._checked = False
        self.tree.error(self.on_app_command_error)

    async def setup_hook(self) -> None:
        for extension in EXTENSIONS:
            await self.load_extension(extension)
        synced = await self.tree.sync()
        log.info("Synced %d slash command(s)", len(synced))

    async def on_ready(self) -> None:
        log.info("Logged in as %s (discord.py %s)", self.user, discord.__version__)
        if not self._checked:  # on_ready also fires after reconnects
            self._checked = True
            await run_startup_checks(self)

    def reply(self, kind: str, text: str) -> str:
        """Prefix a message with the configured emoji for success, warning, error, etc."""
        return f"{getattr(self.config.emoji, kind)} {text}"

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            message = str(error) or "You can't use this command here."
        else:
            log.error("Command /%s failed", interaction.command and interaction.command.name, exc_info=error)
            message = "Something went wrong running that command. The error has been logged."
        message = self.reply("error", message)
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
