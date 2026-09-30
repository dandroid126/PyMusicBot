"""The player's track-end logic, with a fake voice connection instead of Discord."""

import asyncio
import random
from types import SimpleNamespace

import pytest

from pymusicbot.audio.player import GuildPlayer
from pymusicbot.audio.sources import Resolved, SourceError
from pymusicbot.audio.track import Requester, Track
from pymusicbot.config import load_config
from pymusicbot.playlists import PlaylistStore
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

    async def resolve_each(self, items, requester):
        for index, item in enumerate(items):
            # "album:a,b,c" stands for a folder entry that holds several songs
            names = item.removeprefix("album:").split(",") if item.startswith("album:") else [item]
            yield index, item, Resolved([track(name, requester=None) for name in names])


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
        playlists=PlaylistStore(tmp_path / "Playlists"),
        on_track_change=lambda player: None,
        get_channel=lambda channel_id: None,
        get_guild=lambda guild_id: None,
        shutting_down=False,
        reply=lambda kind, text: text,
    )
    player = FakePlayer(bot, GUILD)
    player.fake_voice = FakeVoice()
    return player


def track(name, requester=1):
    return Track(
        title=name,
        source=f"/music/{name}.mp3",
        duration=60,
        requester=Requester(requester, "user") if requester else None,
        local=True,
    )


def default_playlist(player, *items):
    player.bot.playlists.folder.mkdir(exist_ok=True)
    (player.bot.playlists.folder / "auto.txt").write_text("\n".join(items), encoding="utf-8")
    player.bot.settings.update(GUILD, default_playlist="auto")


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


def test_autoplay_when_queue_runs_out_and_requests_go_first(player):
    async def scenario():
        default_playlist(player, "x", "y")
        await player.add(track("a"))
        player.fake_voice.finish()
        await settle()
        assert playing(player) == "x"
        assert player.current.requester is None
        await player.add(track("b"))
        player.fake_voice.finish()
        await settle()
        assert playing(player) == "b"  # a request plays before the rest of autoplay
        player.fake_voice.finish()
        await settle()
        assert playing(player) == "y"
        player.fake_voice.finish()
        await settle()
        assert playing(player) == "x"  # the default playlist loops
        assert not player.fake_voice.disconnected

    run(scenario())


def test_autoplay_that_cannot_play_anything_gives_up(player):
    async def scenario():
        default_playlist(player, "x", "y")
        player.bot.sources.broken.update({"x", "y"})
        await player.add(track("a"))
        player.fake_voice.finish()
        await settle()
        assert playing(player) is None
        assert player.fake_voice.disconnected

    run(scenario())


def test_stop_clears_autoplay(player):
    async def scenario():
        default_playlist(player, "x", "y")
        await player.add(track("a"))
        player.fake_voice.finish()
        await settle()
        await player.stop()
        await settle()
        assert playing(player) is None
        assert player.autoplay == []

    run(scenario())


def test_changing_the_default_playlist_applies_from_the_next_song(player):
    async def scenario():
        default_playlist(player, "x", "y")
        await player.add(track("a"))
        player.fake_voice.finish()
        await settle()
        assert playing(player) == "x"

        (player.bot.playlists.folder / "other.txt").write_text("p\nq", encoding="utf-8")
        player.bot.settings.update(GUILD, default_playlist="other")
        await player.reload_autoplay()
        assert playing(player) == "x"  # the current song finishes
        player.fake_voice.finish()
        await settle()
        assert playing(player) == "p"

    run(scenario())


def test_new_default_playlist_starts_right_away_when_idle_in_voice(player):
    async def scenario():
        default_playlist(player, "x", "y")
        await player.reload_autoplay()  # connected, nothing playing
        await settle()
        assert playing(player) == "x"

    run(scenario())


def play_through(player, count):
    """Let `count` songs finish, returning the titles that played."""
    async def go():
        titles = []
        for _ in range(count):
            titles.append(playing(player))
            player.fake_voice.finish()
            await settle()
        return titles
    return go()


def test_shuffle_mode_plays_every_song_once_per_pass_and_mixes_folders(player):
    async def scenario():
        random.seed(3)
        default_playlist(player, "#shuffle", "album:a,b,c,d", "e", "album:f,g")
        await player.reload_autoplay()
        await settle()
        first_pass = await play_through(player, 7)
        second_pass = await play_through(player, 7)
        assert sorted(first_pass) == sorted(second_pass) == list("abcdefg")
        assert first_pass != list("abcdefg")
        folder_positions = [first_pass.index(n) for n in "abcd"]
        assert folder_positions != list(range(folder_positions[0], folder_positions[0] + 4))  # not kept together

    run(scenario())


def test_random_mode_picks_any_song_each_time(player):
    async def scenario():
        random.seed(5)
        default_playlist(player, "#random", "album:a,b,c")
        await player.reload_autoplay()
        await settle()
        titles = await play_through(player, 12)
        assert set(titles) == {"a", "b", "c"}
        # Unlike shuffle mode, a song can come back before the others have all played.
        assert any(len(set(titles[i:i + 3])) < 3 for i in range(0, len(titles), 3))

    run(scenario())


def test_random_mode_drops_songs_that_fail(player):
    async def scenario():
        random.seed(1)
        player.bot.sources.broken.add("bad")
        default_playlist(player, "#random", "album:bad,good")
        await player.reload_autoplay()
        await settle()
        titles = await play_through(player, 6)
        assert set(titles) == {"good"}

    run(scenario())


def test_song_ending_during_shutdown_does_nothing(player):
    async def scenario():
        default_playlist(player, "x", "y")
        await player.add(track("a"))
        await player.add(track("b"))
        player.bot.shutting_down = True
        player.halt()
        player.fake_voice.finish()  # discord.py leaving voice ends the song
        await settle()
        assert playing(player) is None
        assert queued(player) == []
        assert not player.fake_voice.disconnected  # disconnecting is left to discord.py

    run(scenario())
