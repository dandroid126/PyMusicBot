"""The per-server player: voice connection, current track, queue and what happens when a track ends.

All state changes happen on the event loop. discord.py calls a track's `after` callback from
its audio thread, so that callback only schedules `_on_play_end` back onto the loop.
"""

from __future__ import annotations

import asyncio
import logging
import random
import shlex
import time
from typing import TYPE_CHECKING

import discord
from discord.ext import tasks

from ..playlists import Playlist, ShuffleMode
from ..settings import QueueType, RepeatMode
from .queue import TrackQueue
from .sources import SourceError
from .track import Track

if TYPE_CHECKING:
    from ..bot import MusicBot

log = logging.getLogger(__name__)

def log_failure(future: asyncio.Future) -> None:
    """Done-callback for background work, so its errors reach the log instead of vanishing."""
    if not future.cancelled() and future.exception() is not None:
        log.error("Background player task failed", exc_info=future.exception())


FRAME_SECONDS = 0.02  # discord.py sends audio in 20 ms frames
RECONNECT = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"


class TrackAudio(discord.PCMVolumeTransformer):
    """A track's decoded audio at an adjustable volume. Counts frames to know the position."""

    def __init__(self, original: discord.FFmpegPCMAudio, volume: float, start: float):
        super().__init__(original, volume)
        self.start = start
        self.frames = 0

    def read(self) -> bytes:
        data = super().read()
        if data:
            self.frames += 1
        return data

    @property
    def position(self) -> float:
        return self.start + self.frames * FRAME_SECONDS


class GuildPlayer:
    def __init__(self, bot: MusicBot, guild_id: int):
        self.bot = bot
        self.guild_id = guild_id
        settings = bot.settings.get(guild_id)
        self.queue: TrackQueue[Track] = TrackQueue(fair=settings.queue_type == QueueType.FAIR)
        self.volume = settings.volume
        self.current: Track | None = None
        self.audio: TrackAudio | None = None
        self.votes: set[int] = set()  # members who voted to skip the current track
        self.text_channel_id: int | None = None  # where playback problems are reported: the last music command's channel
        self._play_id = 0  # identifies the running voice.play() call; a seek keeps it
        self._end_reason: str | None = None  # "skip" or "stop" when the bot ends a track itself
        # Autoplay: tracks from the server's default playlist, played when the queue is empty.
        # Requests always go ahead of these.
        self.autoplay: list[Track] = []
        self._autoplay_pool: list[Track] = []  # random mode: every song, picked from at random
        self._autoplay_task: asyncio.Task | None = None
        self._played_since_autoplay_load = True

    # State

    @property
    def guild(self) -> discord.Guild | None:
        return self.bot.get_guild(self.guild_id)

    @property
    def voice(self) -> discord.VoiceClient | None:
        voice = self.guild.voice_client if self.guild else None
        return voice if isinstance(voice, discord.VoiceClient) and voice.is_connected() else None

    @property
    def is_active(self) -> bool:
        """A track is loaded and the bot is in a voice channel (JMusicBot's isMusicPlaying)."""
        return self.current is not None and self.voice is not None

    @property
    def paused(self) -> bool:
        voice = self.voice
        return bool(voice and voice.is_paused())

    @property
    def position(self) -> float:
        return self.audio.position if self.audio else 0.0

    # Connection

    async def connect(self, channel: discord.VoiceChannel | discord.StageChannel) -> None:
        voice = self.voice
        if voice is None:
            await channel.connect(self_deaf=True)
        elif voice.channel != channel:
            await voice.move_to(channel)

    async def disconnect(self) -> None:
        voice = self.voice
        if voice is not None:
            await voice.disconnect()

    # Queueing

    async def add(self, track: Track, *, front: bool = False) -> int:
        """Queue a track. Returns -1 if it starts playing right away, otherwise its queue index."""
        if front:
            self.queue.add_at(0, track)
            index = 0
        else:
            index = self.queue.add(track)
        if self.current is None:
            await self._advance()
            return -1
        return index

    async def add_many(self, tracks: list[Track]) -> None:
        for track in tracks:
            self.queue.add(track)
        if self.current is None:
            await self._advance()

    def set_queue_type(self, queue_type: QueueType) -> None:
        self.queue.fair = queue_type == QueueType.FAIR

    # Controls

    def skip(self) -> None:
        voice = self.voice
        if voice and self.current:
            self._end_reason = "skip"
            voice.stop()

    async def stop(self) -> None:
        """Stop playing, clear the queue and leave the voice channel."""
        log.info("Stopping in %s", self.guild)
        self._clear_queues()
        self._end_reason = "stop"
        self._clear_current()
        voice = self.voice
        if voice:
            voice.stop()
            await voice.disconnect()
        self.bot.on_track_change(self)

    def pause(self) -> None:
        if self.voice:
            self.voice.pause()

    def resume(self) -> None:
        if self.voice:
            self.voice.resume()

    def set_volume(self, volume: int) -> None:
        self.volume = volume
        if self.audio:
            self.audio.volume = volume / 100

    async def seek(self, seconds: float) -> None:
        """Restart the current track's audio at `seconds`. Raises SourceError."""
        track, voice, old = self.current, self.voice, self.audio
        if track is None or voice is None:
            return
        await self.bot.sources.ensure_stream(track)
        audio = self._make_audio(track, seconds)
        was_paused = voice.is_paused()
        self.audio = audio
        voice.source = audio  # swaps the source without ending the play() call
        if was_paused:
            voice.pause()
        if old:
            old.cleanup()

    async def reload_autoplay(self) -> None:
        """Use the server's current default playlist from the next song on.

        Tracks already loaded from the old one are dropped; the song playing now finishes.
        If the bot is idle in voice, the new playlist starts right away.
        """
        self.autoplay.clear()
        self._autoplay_pool.clear()
        if self._autoplay_task:
            self._autoplay_task.cancel()
            self._autoplay_task = None
        self._played_since_autoplay_load = True
        if self.current is None and self.voice is not None:
            await self._advance()

    def halt(self) -> None:
        """Stop reacting before shutdown: clear everything, but leave disconnecting to discord.py."""
        self._clear_queues()
        self._end_reason = "stop"
        self._clear_current()

    def reset(self) -> None:
        """Forget everything after the bot left voice without being told to (kicked, moved out)."""
        self._clear_queues()
        self._end_reason = "stop"
        self._clear_current()
        self.bot.on_track_change(self)

    # Playback

    async def _advance(self) -> None:
        """Play the next track that loads: requests first, then autoplay. Leave when there's none."""
        while track := self._next_track():
            self.current = track  # claimed before any await, so concurrent adds queue behind it
            if await self._start(track, track.start_offset):
                return
            if track.requester is None:  # autoplay; don't keep picking a song that won't play
                self._autoplay_pool = [t for t in self._autoplay_pool if t.source != track.source]
        self._clear_current()
        self.bot.on_track_change(self)
        if self._start_autoplay():
            return  # the default playlist is loading; its first track will start playback
        if self.bot.config.player.stay_in_channel:
            log.info("Queue finished in %s; staying in voice", self.guild)
        else:
            log.info("Queue finished in %s with no default playlist to play; leaving voice", self.guild)
            await self.disconnect()

    def _next_track(self) -> Track | None:
        if self.queue:
            return self.queue.pull()
        self._pick_random()
        if self.autoplay:
            return self.autoplay.pop(0)
        return None

    def _pick_random(self) -> None:
        """In random mode, keep one randomly picked song lined up, so it can be prefetched."""
        if self._autoplay_pool and not self.autoplay:
            self.autoplay.append(random.choice(self._autoplay_pool).fresh_copy())

    def _start_autoplay(self) -> bool:
        """Start loading the default playlist in the background. False if there's nothing to load."""
        if self._autoplay_task and not self._autoplay_task.done():
            return True
        name = self.bot.settings.get(self.guild_id).default_playlist
        if not name or self.voice is None:
            return False
        if not self._played_since_autoplay_load:
            log.warning("Nothing from the default playlist %s could be played; not reloading it", name)
            return False
        playlist = self.bot.playlists.load(name)
        if playlist is None or not playlist.items:
            return False
        self._played_since_autoplay_load = False
        self._autoplay_task = asyncio.create_task(self._load_autoplay(playlist))
        self._autoplay_task.add_done_callback(log_failure)
        return True

    async def _load_autoplay(self, playlist: Playlist) -> None:
        log.info("Loading default playlist %s (%d entries)", playlist.name, len(playlist.items))
        async for _, item, result in self.bot.sources.resolve_each(playlist.items, None):
            if isinstance(result, SourceError):
                log.info("Default playlist %s: skipped %s: %s", playlist.name, item, result)
                continue
            for track in result.tracks:
                if playlist.mode == ShuffleMode.RANDOM:
                    self._autoplay_pool.append(track)
                elif playlist.mode == ShuffleMode.SHUFFLE:
                    # A random spot among the songs still to come: songs from a folder or online
                    # playlist get mixed in with everything else, not kept together.
                    self.autoplay.insert(random.randint(0, len(self.autoplay)), track)
                else:
                    self.autoplay.append(track)
            if self.current is None and self.voice is not None:
                await self._advance()
        idle = self.current is None and not self.queue and not self.autoplay and not self._autoplay_pool
        if idle and self.voice is not None and not self.bot.config.player.stay_in_channel:
            await self.disconnect()

    async def _start(self, track: Track, position: float) -> bool:
        try:
            await self.bot.sources.ensure_stream(track)
        except SourceError as e:
            await self._report(f"Couldn't play {track.title}: {e}")
            return False
        if self.current is not track:  # stopped while loading
            return True
        voice = self.voice
        if voice is None:  # left voice while loading
            self._clear_queues()
            self._clear_current()
            self.bot.on_track_change(self)
            return True
        audio = self._make_audio(track, position)
        self._play_id += 1
        play_id = self._play_id
        self._end_reason = None  # a reason set with nothing playing mustn't apply to this track
        loop = asyncio.get_running_loop()

        def after(error: Exception | None) -> None:
            future = asyncio.run_coroutine_threadsafe(self._on_play_end(play_id, error), loop)
            future.add_done_callback(log_failure)

        self.audio = audio
        self.votes.clear()
        self._played_since_autoplay_load = True
        voice.play(audio, after=after)
        log.info("Playing %s in %s", track.title, voice.channel)
        self.bot.on_track_change(self)
        if not self.queue:
            self._pick_random()
        upcoming = self.queue[0] if self.queue else self.autoplay[0] if self.autoplay else None
        if upcoming:
            asyncio.create_task(self._prefetch(upcoming))
        return True

    async def _on_play_end(self, play_id: int, error: Exception | None) -> None:
        if play_id != self._play_id or self.bot.shutting_down:
            return  # a play() that was already replaced, or the bot is shutting down
        reason, self._end_reason = self._end_reason, None
        if reason == "stop":
            return
        track = self.current
        if error:
            log.error("Playback of %s failed", track.title if track else "a track", exc_info=error)
        elif track and reason is None:  # finished normally: honor repeat mode
            mode = self.bot.settings.get(self.guild_id).repeat_mode
            if mode == RepeatMode.ALL:
                self.queue.add(track.fresh_copy())
            elif mode == RepeatMode.SINGLE:
                self.queue.add_at(0, track.fresh_copy())
        self._clear_current()
        await self._advance()

    def _make_audio(self, track: Track, position: float) -> TrackAudio:
        if track.local:
            source, before = track.source, ""
        else:
            source, before = track.stream_url, RECONNECT
            if track.stream_user_agent:
                before += f" -user_agent {shlex.quote(track.stream_user_agent)}"
        if position > 0:
            before += f" -ss {position:.3f}"
        pcm = discord.FFmpegPCMAudio(source, before_options=before.strip(), options="-vn")
        return TrackAudio(pcm, self.volume / 100, position)

    async def _prefetch(self, track: Track) -> None:
        try:
            await self.bot.sources.ensure_stream(track)
        except SourceError:
            pass  # reported if it still fails when its turn comes

    def _clear_queues(self) -> None:
        self.queue.clear()
        self.autoplay.clear()
        self._autoplay_pool.clear()
        if self._autoplay_task:
            self._autoplay_task.cancel()
            self._autoplay_task = None
        self._played_since_autoplay_load = True

    def _clear_current(self) -> None:
        self.current = None
        self.audio = None
        self.votes.clear()

    async def _report(self, text: str) -> None:
        log.warning("%s", text)
        channel = self.bot.get_channel(self.text_channel_id) if self.text_channel_id else None
        if isinstance(channel, discord.abc.Messageable):
            try:
                await channel.send(self.bot.reply("error", discord.utils.escape_markdown(text)))
            except discord.HTTPException:
                pass


class PlayerManager:
    def __init__(self, bot: MusicBot):
        self.bot = bot
        self._players: dict[int, GuildPlayer] = {}
        self._alone_since: dict[int, float] = {}  # guild ID -> when the bot was left alone

    def start(self) -> None:
        if self.bot.config.player.alone_time_until_stop > 0:
            self._stop_when_alone.start()

    def get(self, guild_id: int) -> GuildPlayer:
        if guild_id not in self._players:
            self._players[guild_id] = GuildPlayer(self.bot, guild_id)
        return self._players[guild_id]

    def find(self, guild_id: int) -> GuildPlayer | None:
        return self._players.get(guild_id)

    def shutdown(self) -> None:
        self._stop_when_alone.cancel()
        for player in self._players.values():
            player.halt()

    async def autostart(self, guild: discord.Guild) -> None:
        """Like JMusicBot at startup: with a default playlist and a /setvc channel, join it and play.

        Also used when either setting changes, so a new setup never needs a restart. Does
        nothing if the bot is already playing or has queued songs there.
        """
        settings = self.bot.settings.get(guild.id)
        channel = guild.get_channel(settings.voice_channel_id or 0)
        if not settings.default_playlist or not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            return
        player = self.get(guild.id)
        if player.current is not None or player.queue:
            return
        if self.bot.playlists.load(settings.default_playlist) is None:
            log.warning("Default playlist %s for %s doesn't exist", settings.default_playlist, guild)
            return
        try:
            await player.connect(channel)
        except (discord.ClientException, TimeoutError) as e:
            log.warning("Couldn't join %s in %s to start the default playlist: %s", channel, guild, e)
            return
        log.info("Starting default playlist %s in %s", settings.default_playlist, channel)
        await player.reload_autoplay()

    async def default_playlist_edited(self, name: str) -> None:
        """A playlist file changed; servers using it as their default pick up the change."""
        for player in list(self._players.values()):
            if self.bot.settings.get(player.guild_id).default_playlist == name:
                await player.reload_autoplay()

    def active(self) -> list[GuildPlayer]:
        return [p for p in self._players.values() if p.is_active]

    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        self._check_alone(member.guild)
        if member.id != self.bot.user.id or after.channel is not None:
            return
        player = self.find(member.guild.id)
        if player is None:
            return
        await asyncio.sleep(1)  # let discord.py finish tearing down (or reconnecting) the voice client
        if player.voice is None and (player.current or player.queue):
            log.info("Left voice in %s; clearing the queue", member.guild)
            player.reset()

    def _check_alone(self, guild: discord.Guild) -> None:
        """Note when nobody is listening in the bot's channel (bots and deafened members don't count)."""
        if self.bot.config.player.alone_time_until_stop <= 0:
            return
        player = self.find(guild.id)
        voice = player.voice if player else None
        alone = voice is not None and not any(
            not m.bot and not (m.voice and (m.voice.deaf or m.voice.self_deaf)) for m in voice.channel.members
        )
        if alone:
            self._alone_since.setdefault(guild.id, time.monotonic())
        else:
            self._alone_since.pop(guild.id, None)

    @tasks.loop(seconds=5)
    async def _stop_when_alone(self) -> None:
        limit = self.bot.config.player.alone_time_until_stop
        for guild_id, since in list(self._alone_since.items()):
            if time.monotonic() - since < limit:
                continue
            self._alone_since.pop(guild_id, None)
            if player := self.find(guild_id):
                log.info("Alone in voice for %ds in %s; stopping", limit, player.guild)
                await player.stop()
