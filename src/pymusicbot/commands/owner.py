"""Commands for the bot owner only."""

from __future__ import annotations

import asyncio
import io
import logging
import platform
import sys
from typing import Literal

import aiohttp
import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands

from .. import __version__
from ..audio.sources import deno_path
from ..bot import MusicBot
from ..checks import Denied, owner_only
from ..presence import STATUSES, parse_activity

log = logging.getLogger(__name__)

MAX_AVATAR_BYTES = 10 * 1024 * 1024


class Owner(commands.Cog):
    def __init__(self, bot: MusicBot):
        self.bot = bot

    @app_commands.command(description="Shut the bot down (owner only)")
    @owner_only
    async def shutdown(self, interaction: discord.Interaction[MusicBot]) -> None:
        log.info("Shutdown requested by %s", interaction.user)
        await interaction.response.send_message(self.bot.reply("warning", "Shutting down..."), ephemeral=True)
        await self.bot.close()

    @app_commands.command(description="Change the bot's activity (owner only)")
    @app_commands.describe(
        activity="Like 'Playing chess', 'Listening to lofi', 'Watching the queue' or 'Streaming <twitch user> <title>'. "
        "Leave empty to clear it."
    )
    @owner_only
    async def setgame(self, interaction: discord.Interaction[MusicBot], activity: str | None = None) -> None:
        self.bot.base_activity = parse_activity(activity or "NONE")
        await self.bot.refresh_presence()
        if self.bot.base_activity is None:
            text = f"**{self.bot.user.name}** is no longer showing an activity."
        else:
            text = f"**{self.bot.user.name}**'s activity is now `{activity.strip()}`. It shows under the bot's name in the member list."
        if self.bot.config.presence.song_in_status and len(self.bot.players.active()) == 1:
            text += "\nRight now the current song is shown instead, because `song_in_status` is on. This activity returns when playback stops."
        await interaction.response.send_message(self.bot.reply("success", text), ephemeral=True)

    @app_commands.command(description="Change the bot's online status (owner only)")
    @owner_only
    async def setstatus(
        self, interaction: discord.Interaction[MusicBot], status: Literal["online", "idle", "dnd", "invisible"]
    ) -> None:
        self.bot.base_status = STATUSES[status]
        await self.bot.refresh_presence()
        await interaction.response.send_message(self.bot.reply("success", f"Set the status to `{status.upper()}`"), ephemeral=True)

    @app_commands.command(description="Change the bot's username (owner only)")
    @owner_only
    async def setname(self, interaction: discord.Interaction[MusicBot], name: app_commands.Range[str, 2, 32]) -> None:
        old = self.bot.user.name
        try:
            await self.bot.user.edit(username=name)
        except discord.HTTPException as e:
            reason = "Discord only allows a couple of name changes per hour." if e.status == 429 else e.text
            raise Denied(f"The name couldn't be changed: {reason}") from None
        await interaction.response.send_message(self.bot.reply("success", f"Name changed from `{old}` to `{name}`"), ephemeral=True)

    @app_commands.command(description="Change the bot's avatar from an image or image URL (owner only)")
    @owner_only
    async def setavatar(
        self, interaction: discord.Interaction[MusicBot], image: discord.Attachment | None = None, url: str | None = None
    ) -> None:
        if image is None and not url:
            raise Denied("Attach an image or give an image URL.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            data = await image.read() if image else await _download_image(url)
            await self.bot.user.edit(avatar=data)
        except (ValueError, aiohttp.ClientError, asyncio.TimeoutError) as e:
            await interaction.followup.send(self.bot.reply("error", f"Couldn't load the image: {e}"))
            return
        except discord.HTTPException as e:
            await interaction.followup.send(self.bot.reply("error", f"Failed to set the avatar: {e.text}"))
            return
        await interaction.followup.send(self.bot.reply("success", "Successfully changed the avatar."))

    @app_commands.command(description="Show debugging information (owner only)")
    @owner_only
    async def debug(self, interaction: discord.Interaction[MusicBot]) -> None:
        config = self.bot.config
        ffmpeg = await _first_line("ffmpeg", "-version")
        deno = await _first_line(deno_path() or "deno", "--version")
        memory_mb = _peak_memory_mb()
        lines = [
            "PyMusicBot Information:",
            f"  Version = {__version__}",
            f"  Owner = {self.bot.owner_id or 'application owner'}",
            f"  MaxTrackLength = {config.player.max_track_length}",
            f"  NowPlayingImages = {config.player.now_playing_images}",
            f"  SongInStatus = {config.presence.song_in_status}",
            f"  StayInChannel = {config.player.stay_in_channel}",
            f"  AloneTimeUntilStop = {config.player.alone_time_until_stop}",
            f"  MusicFolders = {', '.join(str(f) for f in config.files.music_folders)}",
            f"  PlaylistsFolder = {config.files.playlists_folder}",
            "",
            "Dependency Information:",
            f"  Python = {platform.python_version()}",
            f"  discord.py = {discord.__version__}",
            f"  yt-dlp = {yt_dlp.version.__version__}",
            f"  FFmpeg = {ffmpeg}",
            f"  Deno = {deno}",
            "",
            "Runtime Information:",
            f"  Peak Memory = {f'{memory_mb:.0f} MB' if memory_mb is not None else 'unknown'}",
            f"  Active Players = {len(self.bot.players.active())}",
            "",
            "Discord Information:",
            f"  ID = {self.bot.user.id}",
            f"  Servers = {len(self.bot.guilds)}",
            f"  Latency = {self.bot.latency * 1000:.0f} ms",
        ]
        file = discord.File(io.BytesIO("\n".join(lines).encode()), filename="debug_information.txt")
        await interaction.response.send_message(file=file, ephemeral=True)


def _peak_memory_mb() -> float | None:
    """Peak memory of this process. The resource module is Unix-only; Windows asks the OS."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                )
            ]

        # Declare the types: by default ctypes passes the process handle as a 32-bit int,
        # which is wrong on 64-bit Windows and makes the call fail.
        get_process = ctypes.windll.kernel32.GetCurrentProcess
        get_process.restype = wintypes.HANDLE
        get_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        get_info.restype = wintypes.BOOL
        counters = Counters(cb=ctypes.sizeof(Counters))
        if not get_info(get_process(), ctypes.byref(counters), counters.cb):
            return None
        return counters.PeakWorkingSetSize / 1024 / 1024
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1024 / 1024 if sys.platform == "darwin" else peak / 1024  # macOS reports bytes, Linux KB


async def _download_image(url: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session, session.get(url.strip("<>")) as response:
        response.raise_for_status()
        if not response.content_type.startswith("image/"):
            raise ValueError("that URL isn't an image")
        data = await response.content.read(MAX_AVATAR_BYTES + 1)
        if len(data) > MAX_AVATAR_BYTES:
            raise ValueError("the image is larger than 10 MB")
        return data


async def _first_line(*command: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    except FileNotFoundError:
        return "not found"
    out, _ = await proc.communicate()
    return out.decode(errors="replace").splitlines()[0] if out else "unknown"


async def setup(bot: MusicBot) -> None:
    await bot.add_cog(Owner(bot))
