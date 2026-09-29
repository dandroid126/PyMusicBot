"""The per-server player: voice connection, current track, queue and what happens when a track ends.

All state changes happen on the event loop. discord.py calls a track's `after` callback from
its audio thread, so that callback only schedules `_on_play_end` back onto the loop.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from typing import TYPE_CHECKING

import discord

from ..settings import QueueType, RepeatMode
from .queue import TrackQueue
from .sources import SourceError
from .track import Track

if TYPE_CHECKING:
    from ..bot import MusicBot

log = logging.getLogger(__name__)

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
        self.text_channel_id: int | None = None  # where playback problems are reported
        self._play_id = 0  # identifies the running voice.play() call; a seek keeps it
        self._end_reason: str | None = None  # "skip" or "stop" when the bot ends a track itself

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
        self.queue.clear()
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

    def reset(self) -> None:
        """Forget everything after the bot left voice without being told to (kicked, moved out)."""
        self.queue.clear()
        self._end_reason = "stop"
        self._clear_current()
        self.bot.on_track_change(self)

    # Playback

    async def _advance(self) -> None:
        """Play the next queued track that loads, or finish when the queue is empty."""
        while self.queue:
            track = self.queue.pull()
            self.current = track  # claimed before any await, so concurrent adds queue behind it
            if await self._start(track, track.start_offset):
                return
        self._clear_current()
        self.bot.on_track_change(self)
        if not self.bot.config.player.stay_in_channel:
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
            self.queue.clear()
            self._clear_current()
            self.bot.on_track_change(self)
            return True
        audio = self._make_audio(track, position)
        self._play_id += 1
        play_id = self._play_id
        self._end_reason = None  # a reason set with nothing playing mustn't apply to this track
        loop = asyncio.get_running_loop()

        def after(error: Exception | None) -> None:
            asyncio.run_coroutine_threadsafe(self._on_play_end(play_id, error), loop)

        self.audio = audio
        self.votes.clear()
        voice.play(audio, after=after)
        log.info("Playing %s in %s", track.title, voice.channel)
        self.bot.on_track_change(self)
        if self.queue:
            asyncio.create_task(self._prefetch(self.queue[0]))
        return True

    async def _on_play_end(self, play_id: int, error: Exception | None) -> None:
        if play_id != self._play_id:
            return  # a play() that was already replaced
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

    def get(self, guild_id: int) -> GuildPlayer:
        if guild_id not in self._players:
            self._players[guild_id] = GuildPlayer(self.bot, guild_id)
        return self._players[guild_id]

    def find(self, guild_id: int) -> GuildPlayer | None:
        return self._players.get(guild_id)

    def active(self) -> list[GuildPlayer]:
        return [p for p in self._players.values() if p.is_active]

    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if member.id != self.bot.user.id or after.channel is not None:
            return
        player = self.find(member.guild.id)
        if player is None:
            return
        await asyncio.sleep(1)  # let discord.py finish tearing down (or reconnecting) the voice client
        if player.voice is None and (player.current or player.queue):
            log.info("Left voice in %s; clearing the queue", member.guild)
            player.reset()
