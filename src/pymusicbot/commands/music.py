"""Music commands anyone can use, with JMusicBot's rules about where and when."""

from __future__ import annotations

import logging
import math

import discord
from discord import app_commands
from discord.ext import commands

from ..audio.nowplaying import now_playing
from ..audio.player import GuildPlayer
from ..audio.sources import Resolved, SourceError, attached_playlist, youtube_video_id
from ..audio.track import Requester
from ..bot import MusicBot
from ..checks import Denied, is_dj
from ..formatting import PAUSE_EMOJI, PLAY_EMOJI, linked_title, queue_line, title
from ..settings import QueueType, RepeatMode
from ..timeutil import format_time, parse_seek
from ..views import ConfirmView, PagedView, PickView

log = logging.getLogger(__name__)

QUEUE_PAGE_SIZE = 10
QUEUE_TYPE_EMOJI = {QueueType.LINEAR: "⏩", QueueType.FAIR: "🔢"}
REPEAT_EMOJI = {RepeatMode.OFF: "", RepeatMode.ALL: "🔁", RepeatMode.SINGLE: "🔂"}
VoiceChannel = discord.VoiceChannel | discord.StageChannel


def requester_of(member: discord.Member | discord.User) -> Requester:
    return Requester(member.id, member.display_name, member.display_avatar.url)


def music_checks(
    interaction: discord.Interaction[MusicBot], *, listening: bool = False, playing: bool = False, joining: bool = False
) -> tuple[GuildPlayer, VoiceChannel | None]:
    """JMusicBot's MusicCommand rules. Returns the player and, with `listening` or `joining`, the voice channel to use.

    - `playing`: something must be playing.
    - `listening`: the member must be in voice, not deafened, not in the AFK channel, and in the
      bot's channel (or the /setvc channel when the bot isn't connected yet).
    - `joining` (commands that add music): when a /setvc channel is configured, the bot plays
      there and the member doesn't need to be in voice. Otherwise the same as `listening`.
    """
    bot, guild, member = interaction.client, interaction.guild, interaction.user
    settings = bot.settings.get(guild.id)

    player = bot.players.get(guild.id)
    if playing and not player.is_active:
        raise Denied("There must be music playing to use that!")

    channel = None
    if joining:
        configured = guild.get_channel(settings.voice_channel_id or 0)
        if isinstance(configured, (discord.VoiceChannel, discord.StageChannel)):
            voice = player.voice
            if voice is None:
                permissions = configured.permissions_for(guild.me)
                if not (permissions.connect and permissions.speak):
                    raise Denied(f"I am unable to connect to {configured.mention}!")
            player.text_channel_id = interaction.channel_id
            return player, voice.channel if voice else configured
        listening = True  # no configured channel: play in the member's channel

    if listening:
        voice = player.voice
        current = voice.channel if voice else guild.get_channel(settings.voice_channel_id or 0)
        state = member.voice
        if state is None or state.channel is None or state.deaf or state.self_deaf or (current and state.channel != current):
            raise Denied(f"You must be listening in {current.mention if current else 'a voice channel'} to use that!")
        if guild.afk_channel and state.channel == guild.afk_channel:
            raise Denied("You cannot use that command in an AFK channel!")
        permissions = state.channel.permissions_for(guild.me)
        if voice is None and not (permissions.connect and permissions.speak):
            raise Denied(f"I am unable to connect to {state.channel.mention}!")
        channel = state.channel

    player.text_channel_id = interaction.channel_id
    return player, channel


async def library_choices(bot: MusicBot, current: str) -> list[app_commands.Choice[str]]:
    """Autocomplete suggestions from the music library for a query option."""
    if current.startswith(("http://", "https://")):
        return []
    paths = await bot.library.search(current)
    return [app_commands.Choice(name=p, value=p) for p in paths if len(p) <= 100]


class Music(commands.Cog):
    def __init__(self, bot: MusicBot):
        self.bot = bot

    # Playing

    @app_commands.command(description="Play a song, playlist, or library file or folder; with no query, resume or start autoplay")
    @app_commands.describe(query="A URL, a file or folder from the music library, or words to search YouTube for")
    @app_commands.guild_only()
    async def play(self, interaction: discord.Interaction[MusicBot], query: str | None = None) -> None:
        if not query:
            await self._resume_or_help(interaction)
            return
        player, channel = music_checks(interaction, joining=True)
        await interaction.response.defer(thinking=True)
        requester = requester_of(interaction.user)
        try:
            resolved = await self.bot.sources.resolve(query, requester)
        except SourceError as e:
            await interaction.followup.send(self.bot.reply("error", f"Couldn't load that: {discord.utils.escape_markdown(str(e))}"))
            return
        added = await self._enqueue(player, channel, resolved)

        playlist_url = attached_playlist(query.strip().removeprefix("<").removesuffix(">"))
        if not (playlist_url and resolved.playlist_title is None and resolved.tracks and player.voice):
            await interaction.followup.send(added)
            return

        first = resolved.tracks[0]

        async def load_playlist(click: discord.Interaction) -> None:
            await click.response.edit_message(content=added + "\n" + self.bot.reply("loading", "Loading the playlist..."), view=None)
            try:
                playlist = await self.bot.sources.resolve(playlist_url, requester)
            except SourceError as e:
                await click.edit_original_response(content=added + "\n" + self.bot.reply("error", f"Couldn't load the playlist: {e}"))
                return
            tracks = [t for t in playlist.tracks if youtube_video_id(t.source) != youtube_video_id(first.source)]
            await player.add_many(tracks)
            await click.edit_original_response(content=added + "\n" + self.bot.reply("success", f"Loaded **{len(tracks)}** additional tracks!"))

        view = ConfirmView(interaction.user.id, "Load playlist", "📥", added, load_playlist)
        view.message = await interaction.followup.send(
            added + "\n" + self.bot.reply("warning", "This track is part of a playlist. Load the rest of it?"),
            view=view,
            wait=True,
        )

    @play.autocomplete("query")
    async def play_autocomplete(self, interaction: discord.Interaction[MusicBot], current: str) -> list[app_commands.Choice[str]]:
        return await library_choices(self.bot, current)

    @app_commands.command(description="Search YouTube and pick a result to play")
    @app_commands.guild_only()
    async def search(self, interaction: discord.Interaction[MusicBot], query: str) -> None:
        await self._search(interaction, query, "ytsearch", "YouTube")

    @app_commands.command(description="Search SoundCloud and pick a result to play")
    @app_commands.guild_only()
    async def scsearch(self, interaction: discord.Interaction[MusicBot], query: str) -> None:
        await self._search(interaction, query, "scsearch", "SoundCloud")

    @app_commands.command(description="Search the music library and pick a file or folder to play")
    @app_commands.describe(query="Words in the file or folder name")
    @app_commands.guild_only()
    async def local(self, interaction: discord.Interaction[MusicBot], query: str) -> None:
        player, channel = music_checks(interaction, joining=True)
        paths = await self.bot.library.search(query)
        if not paths:
            await interaction.response.send_message(self.bot.reply("warning", f"Nothing in the music library matches `{query}`."))
            return

        async def picked(pick: discord.Interaction, index: int) -> None:
            await pick.response.edit_message(content=self.bot.reply("loading", f"Loading `{paths[index]}`..."), view=None)
            try:
                resolved = await self.bot.sources.resolve(paths[index], requester_of(pick.user))
            except SourceError as e:
                await pick.edit_original_response(content=self.bot.reply("error", f"Couldn't load that: {e}"))
                return
            await pick.edit_original_response(content=await self._enqueue(player, channel, resolved))

        options = [discord.SelectOption(label=_clip(p, 100), emoji="📁" if p.endswith("/") else "🎵") for p in paths]
        view = PickView(interaction.user.id, options, picked)
        await interaction.response.send_message(self.bot.reply("success", f"Music library matches for `{query}`:"), view=view)
        view.message = await interaction.original_response()

    # Queue

    @app_commands.command(description="Show the queue")
    @app_commands.guild_only()
    async def queue(self, interaction: discord.Interaction[MusicBot], page: app_commands.Range[int, 1] = 1) -> None:
        player, _ = music_checks(interaction, playing=True)
        tracks = list(player.queue)
        if not tracks:
            content, embed = now_playing(self.bot, player)
            await interaction.response.send_message(self.bot.reply("warning", "There is no music in the queue!"), embed=embed)
            self.bot.now_playing.track(await interaction.original_response())
            return

        settings = self.bot.settings.get(interaction.guild_id)
        total = sum(t.duration or 0 for t in tracks)
        status = PAUSE_EMOJI if player.paused else PLAY_EMOJI
        header = (
            f"{status} {title(player.current)}\n"
            + self.bot.reply("success", f"Current Queue | {len(tracks)} entries | `{format_time(total)}` | ")
            + f"{QUEUE_TYPE_EMOJI[settings.queue_type]} `{settings.queue_type.value.title()}`"
            + (f" | {REPEAT_EMOJI[settings.repeat_mode]}" if settings.repeat_mode != RepeatMode.OFF else "")
        )
        color = interaction.guild.me.color
        pages = []
        for start in range(0, len(tracks), QUEUE_PAGE_SIZE):
            lines = [f"`{start + i + 1}.` {queue_line(t)}" for i, t in enumerate(tracks[start:start + QUEUE_PAGE_SIZE])]
            embed = discord.Embed(description="\n".join(lines), color=color)
            embed.set_footer(text=f"Page {len(pages) + 1}/{math.ceil(len(tracks) / QUEUE_PAGE_SIZE)}")
            pages.append(embed)
        if len(pages) == 1:
            await interaction.response.send_message(header, embed=pages[0])
            return
        view = PagedView(interaction.user.id, pages, start=page - 1)
        await interaction.response.send_message(header, embed=view.embed, view=view)
        view.message = await interaction.original_response()

    @app_commands.command(description="Show the song that's playing")
    @app_commands.guild_only()
    async def nowplaying(self, interaction: discord.Interaction[MusicBot]) -> None:
        player, _ = music_checks(interaction)
        content, embed = now_playing(self.bot, player)
        await interaction.response.send_message(content, embed=embed)
        if player.is_active:
            self.bot.now_playing.track(await interaction.original_response())
        else:
            self.bot.now_playing.forget(interaction.guild_id)

    @app_commands.command(description="Vote to skip the current song (skips right away if you queued it)")
    @app_commands.guild_only()
    async def skip(self, interaction: discord.Interaction[MusicBot]) -> None:
        player, _ = music_checks(interaction, listening=True, playing=True)
        track = player.current
        settings = self.bot.settings.get(interaction.guild_id)
        ratio = settings.skip_ratio if settings.skip_ratio is not None else self.bot.config.player.skip_ratio
        if interaction.user.id == track.requester_id or ratio == 0:
            player.skip()
            await interaction.response.send_message(self.bot.reply("success", f"Skipped {title(track)}"))
            return

        members = player.voice.channel.members
        listeners = [m for m in members if not m.bot and not (m.voice and (m.voice.deaf or m.voice.self_deaf))]
        if interaction.user.id in player.votes:
            message = self.bot.reply("warning", "You already voted to skip this song `[")
        else:
            player.votes.add(interaction.user.id)
            message = self.bot.reply("success", "You voted to skip the song `[")
        skippers = sum(1 for m in members if m.id in player.votes)
        required = math.ceil(len(listeners) * ratio)
        message += f"{skippers} votes, {required}/{len(listeners)} needed]`"
        if skippers >= required:
            by = f"(requested by **{track.requester.name}**)" if track.requester else "(autoplay)"
            message += "\n" + self.bot.reply("success", f"Skipped {title(track)} {by}")
            player.skip()
        await interaction.response.send_message(message)

    @app_commands.command(description="Remove a song from the queue, or all of yours")
    @app_commands.describe(position="Queue position, or 'all' for all of your songs")
    @app_commands.guild_only()
    async def remove(self, interaction: discord.Interaction[MusicBot], position: str) -> None:
        player, _ = music_checks(interaction, listening=True, playing=True)
        if not player.queue:
            raise Denied("There is nothing in the queue!")
        if position.strip().lower() == "all":
            count = player.queue.remove_all(interaction.user.id)
            if count:
                await interaction.response.send_message(self.bot.reply("success", f"Removed your {count} entries."))
            else:
                await interaction.response.send_message(self.bot.reply("warning", "You don't have any songs in the queue!"))
            return
        try:
            index = int(position) - 1
        except ValueError:
            index = -1
        if not 0 <= index < len(player.queue):
            raise Denied(f"Position must be a whole number between 1 and {len(player.queue)}!")
        track = player.queue[index]
        if track.requester_id == interaction.user.id:
            player.queue.remove(index)
            await interaction.response.send_message(self.bot.reply("success", f"Removed {title(track)} from the queue"))
        elif await is_dj(interaction):
            player.queue.remove(index)
            by = f"**{track.requester.name}**" if track.requester else "the bot"
            await interaction.response.send_message(self.bot.reply("success", f"Removed {title(track)} from the queue (requested by {by})"))
        else:
            raise Denied(f"You cannot remove {title(track)} because you didn't add it!")

    @remove.autocomplete("position")
    async def remove_autocomplete(self, interaction: discord.Interaction[MusicBot], current: str) -> list[app_commands.Choice[str]]:
        player = self.bot.players.find(interaction.guild_id)
        choices = [app_commands.Choice(name="all (every song you added)", value="all")]
        if player:
            choices += [
                app_commands.Choice(name=_clip(f"{i + 1}. {t.title}", 100), value=str(i + 1))
                for i, t in enumerate(player.queue)
            ]
        text = current.lower()
        return [c for c in choices if text in c.name.lower()][:25]

    @app_commands.command(description="Shuffle the songs you added")
    @app_commands.guild_only()
    async def shuffle(self, interaction: discord.Interaction[MusicBot]) -> None:
        player, _ = music_checks(interaction, listening=True, playing=True)
        count = player.queue.shuffle(interaction.user.id)
        if count == 0:
            raise Denied("You don't have any music in the queue to shuffle!")
        if count == 1:
            await interaction.response.send_message(self.bot.reply("warning", "You only have one song in the queue!"))
        else:
            await interaction.response.send_message(self.bot.reply("success", f"You successfully shuffled your {count} entries."))

    @app_commands.command(description="Jump to a time in the current song")
    @app_commands.describe(time="Like 1:30, 90, 1m30s, or +30 / -10 to move from where it is")
    @app_commands.guild_only()
    async def seek(self, interaction: discord.Interaction[MusicBot], time: str) -> None:
        player, _ = music_checks(interaction, listening=True, playing=True)
        track = player.current
        if not track.seekable:
            raise Denied("This track is not seekable.")
        if track.requester_id != interaction.user.id and not await is_dj(interaction):
            raise Denied(f"You cannot seek {title(track)} because you didn't add it!")
        parsed = parse_seek(time.strip())
        if parsed is None:
            raise Denied("Invalid seek! Examples: `1:02:23` `+1:10` `-90` `1h10m` `+90s`")
        target = parsed.milliseconds / 1000 + (player.position if parsed.relative else 0)
        target = max(target, 0.0)
        if target > track.duration:
            raise Denied(f"Cannot seek to `{format_time(target)}` because the current track is `{format_time(track.duration)}` long!")
        await interaction.response.defer(thinking=True)
        try:
            await player.seek(target)
        except SourceError as e:
            await interaction.followup.send(self.bot.reply("error", f"Couldn't seek: {e}"))
            return
        await interaction.followup.send(self.bot.reply("success", f"Seeked to `{format_time(target)}/{format_time(track.duration)}`!"))

    # Helpers

    async def _resume_or_help(self, interaction: discord.Interaction[MusicBot]) -> None:
        player, _ = music_checks(interaction)
        if player.is_active and player.paused:
            if not await is_dj(interaction):
                raise Denied("Only DJs can unpause the player!")
            player.resume()
            await interaction.response.send_message(self.bot.reply("success", f"Resumed {title(player.current)}."))
            return

        # Nothing playing: start the server's default playlist, e.g. after a /stop.
        default = self.bot.settings.get(interaction.guild_id).default_playlist
        if player.current is None and default and self.bot.playlists.load(default) is not None:
            player, channel = music_checks(interaction, joining=True)
            await interaction.response.defer(thinking=True)
            try:
                await player.connect(channel)
            except (discord.ClientException, TimeoutError):
                await interaction.followup.send(self.bot.reply("error", f"I couldn't connect to {channel.mention}."))
                return
            await player.reload_autoplay()
            await interaction.followup.send(
                self.bot.reply("success", f"Starting the default playlist **{default}** in {channel.mention}.")
            )
            return

        await interaction.response.send_message(
            self.bot.reply("warning", "Play commands:")
            + "\n`/play <song title>` - plays the first result from YouTube"
            + "\n`/play <URL>` - plays the song, playlist or stream"
            + "\n`/play <file or folder>` - plays from the music library (suggestions appear as you type)"
            + "\n`/play` - resumes the player when it's paused, or starts the default playlist when nothing is playing",
            ephemeral=True,
        )

    async def _search(self, interaction: discord.Interaction[MusicBot], query: str, site: str, site_name: str) -> None:
        player, channel = music_checks(interaction, joining=True)
        await interaction.response.defer(thinking=True)
        try:
            results = await self.bot.sources.search(query, site)
        except SourceError as e:
            await interaction.followup.send(self.bot.reply("error", f"Search failed: {e}"))
            return
        if not results:
            await interaction.followup.send(self.bot.reply("warning", f"No results found for `{query}`."))
            return

        async def picked(pick: discord.Interaction, index: int) -> None:
            track = results[index]
            track.requester = requester_of(pick.user)
            await pick.response.edit_message(content=self.bot.reply("loading", f"Loading {title(track)}..."), embed=None, view=None)
            resolved = Resolved([track])
            limit = self.bot.config.player.max_track_length
            if limit and track.duration and track.duration > limit:
                resolved = Resolved([], too_long=[track])
            await pick.edit_original_response(content=await self._enqueue(player, channel, resolved))

        lines = [f"`{i + 1}.` `[{format_time(t.duration)}]` {linked_title(t)}" for i, t in enumerate(results)]
        embed = discord.Embed(description="\n".join(lines), color=interaction.guild.me.color)
        options = [
            discord.SelectOption(label=_clip(f"{i + 1}. {t.title}", 100), description=_clip(f"{format_time(t.duration)} · {t.uploader or site_name}", 100))
            for i, t in enumerate(results)
        ]
        view = PickView(interaction.user.id, options, picked)
        view.message = await interaction.followup.send(
            self.bot.reply("success", f"{site_name} results for `{query}`:"), embed=embed, view=view, wait=True
        )

    async def _enqueue(self, player: GuildPlayer, channel: VoiceChannel, resolved: Resolved) -> str:
        """Connect, queue what was resolved and describe the result."""
        limit = format_time(self.bot.config.player.max_track_length)
        if not resolved.tracks:
            if resolved.playlist_title is None and resolved.too_long:
                track = resolved.too_long[0]
                return self.bot.reply(
                    "warning",
                    f"This track ({title(track)}) is longer than the allowed maximum: `{format_time(track.duration)}` > `{limit}`",
                )
            return self.bot.reply("warning", f"Every entry in **{resolved.playlist_title}** is longer than the allowed maximum (`{limit}`)")
        try:
            await player.connect(channel)
        except (discord.ClientException, TimeoutError) as e:
            log.warning("Couldn't connect to %s: %s", channel, e)
            return self.bot.reply("error", f"I couldn't connect to {channel.mention}.")

        if resolved.playlist_title is None:
            track = resolved.tracks[0]
            position = await player.add(track)
            where = "to begin playing" if position == -1 else f"to the queue at position {position + 1}"
            return self.bot.reply("success", f"Added {title(track)} (`{format_time(track.duration)}`) {where}")

        await player.add_many(resolved.tracks)
        message = self.bot.reply(
            "success",
            f"Found **{discord.utils.escape_markdown(resolved.playlist_title)}** with `{len(resolved.tracks) + len(resolved.too_long)}` entries; added to the queue!",
        )
        if resolved.too_long:
            message += "\n" + self.bot.reply("warning", f"{len(resolved.too_long)} tracks longer than the allowed maximum (`{limit}`) were left out.")
        return message


def _clip(text: str, length: int) -> str:
    return text if len(text) <= length else text[:length - 1] + "…"


async def setup(bot: MusicBot) -> None:
    await bot.add_cog(Music(bot))
