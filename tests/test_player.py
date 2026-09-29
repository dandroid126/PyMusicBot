"""The player's track-end logic, with a fake voice connection instead of Discord."""

import asyncio
from types import SimpleNamespace

import pytest

from pymusicbot.audio.player import GuildPlayer
from pymusicbot.audio.sources import SourceError
from pymusicbot.audio.track import Requester, Track
from pymusicbot.config import load_config
from pymusicbot.settings import RepeatMode, SettingsStore

GUILD = 1


class FakeVoice:
    def __init__(self):
        self.after = None
        self.paused = False
        self.disconnected = False
        self.channel = "voice channel"

    def is_connected(self):
        return not self.disconnected

    def is_paused(self):
        return self.paused

    def play(self, source, after):
        self.source, self.after = source, after

    def stop(self):
        self.finish()

    def finish(self, error=None):
        after, self.after = self.after, None
        if after:
            after(error)

    async def disconnect(self):
        self.disconnected = True
        self.finish()


class FakeSources:
    def __init__(self):
        self.broken = set()

    async def ensure_stream(self, track):
        if track.title in self.broken:
            raise SourceError("gone")


class FakePlayer(GuildPlayer):
    fake_voice: FakeVoice

    @property
    def voice(self):
        return None if self.fake_voice.disconnected else self.fake_voice

    def _make_audio(self, track, position):
        return SimpleNamespace(position=position, volume=1, cleanup=lambda: None)


@pytest.fixture
def player(tmp_path):
    config = load_config({"DISCORD_TOKEN": "x", "PYMUSICBOT_CONFIG": str(tmp_path / "none.toml"), "PYMUSICBOT_DATA": str(tmp_path)})
    bot = SimpleNamespace(
        config=config,
        settings=SettingsStore.load(tmp_path),
        sources=FakeSources(),
        on_track_change=lambda player: None,
        get_channel=lambda channel_id: None,
        reply=lambda kind, text: text,
    )
    player = FakePlayer(bot, GUILD)
    player.fake_voice = FakeVoice()
    return player


def track(name, requester=1):
    return Track(title=name, source=f"/music/{name}.mp3", duration=60, requester=Requester(requester, "user"), local=True)


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


def run(coro):
    return asyncio.run(coro)


def playing(player):
    return player.current.title if player.current else None


def queued(player):
    return [t.title for t in player.queue]


def test_first_track_plays_and_others_queue_fairly(player):
    async def scenario():
        assert await player.add(track("a1")) == -1
        assert await player.add(track("a2")) == 0
        assert await player.add(track("a3")) == 1
        # Like JMusicBot, the playing track isn't part of the queue's rounds.
        assert await player.add(track("b1", requester=2)) == 1
        assert playing(player) == "a1"
        assert queued(player) == ["a2", "b1", "a3"]

    run(scenario())


def test_natural_end_plays_next_then_leaves(player):
    async def scenario():
        await player.add(track("a"))
        await player.add(track("b"))
        player.fake_voice.finish()
        await settle()
        assert playing(player) == "b"
        player.fake_voice.finish()
        await settle()
        assert playing(player) is None
        assert player.fake_voice.disconnected  # stay_in_channel is off

    run(scenario())


@pytest.mark.parametrize(("mode", "expected"), [(RepeatMode.ALL, ["b", "a"]), (RepeatMode.SINGLE, ["a", "b"])])
def test_repeat_modes(player, mode, expected):
    async def scenario():
        player.bot.settings.update(GUILD, repeat_mode=mode)
        await player.add(track("a"))
        await player.add(track("b"))
        player.fake_voice.finish()
        await settle()
        assert [playing(player), *queued(player)] == expected

    run(scenario())


def test_skip_does_not_repeat(player):
    async def scenario():
        player.bot.settings.update(GUILD, repeat_mode=RepeatMode.ALL)
        await player.add(track("a"))
        await player.add(track("b"))
        player.skip()
        await settle()
        assert playing(player) == "b"
        assert queued(player) == []

    run(scenario())


def test_track_that_fails_to_load_is_skipped(player):
    async def scenario():
        player.bot.sources.broken.add("broken")
        await player.add_many([track("broken"), track("good")])
        assert playing(player) == "good"

    run(scenario())


def test_stop_clears_everything(player):
    async def scenario():
        await player.add(track("a"))
        await player.add(track("b"))
        await player.stop()
        await settle()
        assert playing(player) is None
        assert queued(player) == []
        assert player.fake_voice.disconnected

    run(scenario())


def test_seek_keeps_the_track_playing(player):
    async def scenario():
        await player.add(track("a"))
        await player.seek(30)
        assert player.position == 30
        player.fake_voice.finish()  # the original play() call ending still advances
        await settle()
        assert playing(player) is None

    run(scenario())
