"""The Discord client: loads command groups, syncs slash commands and reports errors."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from .audio.local import LocalLibrary
from .audio.nowplaying import NowPlayingMessages
from .audio.player import GuildPlayer, PlayerManager, log_failure
from .audio.sources import Sources
from .config import Config
from .playlists import PlaylistStore
from . import shutdown
from .presence import STATUSES, parse_activity
from .settings import SettingsStore
from .startup import run_startup_checks

log = logging.getLogger(__name__)

EXTENSIONS = (
    "pymusicbot.commands.general",
    "pymusicbot.commands.music",
    "pymusicbot.commands.dj",
    "pymusicbot.commands.playlists",
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
            # Track titles and requester mentions are shown, never pinged.
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.config = config
        self.settings = settings
        # The presence shown when no song is: from config, changed by /setgame and /setstatus.
        self.base_activity = parse_activity(config.presence.game)
        self.base_status = STATUSES[config.presence.status]
        self.playlists = PlaylistStore(config.files.playlists_folder)
        self.library = LocalLibrary(config.files.music_folders)
        self.sources = Sources(config, self.library)
        self.players = PlayerManager(self)
        self.now_playing = NowPlayingMessages(self)
        self.shutting_down = False
        self._checked = False
        self.tree.error(self.on_app_command_error)

    async def setup_hook(self) -> None:
        shutdown.install(asyncio.get_running_loop(), self.close)
        for extension in EXTENSIONS:
            await self.load_extension(extension)
        synced = await self.tree.sync()
        log.info("Synced %d slash command(s)", len(synced))
        self.now_playing.start()
        self.players.start()

    async def close(self) -> None:
        """Stop the players quietly first, so nothing reacts while discord.py leaves voice.

        Leaving voice ends the current song; without this, the player would try to start the
        next one or leave voice itself while discord.py is already doing that.
        """
        if not self.shutting_down:
            self.shutting_down = True
            self.now_playing.stop()
            self.players.shutdown()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (discord.py %s)", self.user, discord.__version__)
        if not self._checked:  # on_ready also fires after reconnects
            self._checked = True
            await run_startup_checks(self)
            for guild in self.guilds:
                try:
                    await self.players.autostart(guild)
                except Exception:
                    log.exception("Couldn't start the default playlist in %s", guild)

    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        await self.players.on_voice_state_update(member, before, after)

    def reply(self, kind: str, text: str) -> str:
        """Prefix a message with the configured emoji for success, warning, error, etc."""
        return f"{getattr(self.config.emoji, kind)} {text}"

    def on_track_change(self, player: GuildPlayer) -> None:
        if self.config.presence.song_in_status:
            asyncio.create_task(self.refresh_presence()).add_done_callback(log_failure)

    async def refresh_presence(self) -> None:
        """Show the base presence, or with song_in_status the current song while exactly one server plays."""
        activity = self.base_activity
        active = self.players.active()
        if self.config.presence.song_in_status and len(active) == 1:
            activity = discord.Activity(type=discord.ActivityType.listening, name=active[0].current.title[:128])
        await self.change_presence(status=self.base_status, activity=activity)

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
