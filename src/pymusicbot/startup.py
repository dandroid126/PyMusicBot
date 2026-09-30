"""Checks run once the bot is connected, so setup problems show up in the log right away."""

from __future__ import annotations

import logging
import shutil
from typing import TYPE_CHECKING

import discord

from .audio.sources import deno_path

if TYPE_CHECKING:
    from .bot import MusicBot

log = logging.getLogger(__name__)

# What the bot needs in a server: see channels, talk in text channels, and join and speak in voice.
INVITE_PERMISSIONS = discord.Permissions(
    view_channel=True,
    send_messages=True,
    embed_links=True,
    read_message_history=True,
    connect=True,
    speak=True,
)


def invite_url(bot: MusicBot) -> str:
    return discord.utils.oauth_url(
        bot.application_id, permissions=INVITE_PERMISSIONS, scopes=("bot", "applications.commands")
    )


async def run_startup_checks(bot: MusicBot) -> None:
    if bot.guilds:
        log.info("In %d server(s): %s", len(bot.guilds), ", ".join(f"{g.name} ({g.id})" for g in bot.guilds))
    else:
        log.warning("The bot isn't in any servers yet. Invite it with: %s", invite_url(bot))

    app = await bot.application_info()
    if app.bot_public:
        log.warning(
            "'Public Bot' is on, so anyone can add this bot to their server. Turn it off at "
            "https://discord.com/developers/applications/%s/bot",
            app.id,
        )
    owner = f"user {bot.owner_id}" if bot.owner_id else f"application owner {app.owner or app.team}"
    log.info("Bot owner: %s", owner)

    for program in ("ffmpeg", "ffprobe"):
        if shutil.which(program) is None:
            log.warning("%s wasn't found; playback won't work without it", program)
    if deno_path() is None:
        log.warning("Deno wasn't found; YouTube won't play without it. Reinstall requirements.txt")

    files = bot.config.files
    for folder in files.music_folders:
        if not folder.is_dir():
            log.warning("Music folder %s doesn't exist; local files there can't be played", folder)
    if not files.playlists_folder.is_dir():
        log.info("Playlists folder %s doesn't exist yet", files.playlists_folder)
