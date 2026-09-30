"""Startup autoplay, leave when alone, recovering from being kicked, the now-playing message, shutdown."""

import asyncio
import os
import signal
import sys
from types import SimpleNamespace

import pytest

from conftest import run
from fakes import BOT_ID, GUILD_ID, VOICE_CHANNEL_ID, settle, wait_until
from pymusicbot import shutdown
from pymusicbot.audio import player as player_module
from pymusicbot.audio.nowplaying import now_playing
from pymusicbot.audio.track import Requester, Track


def track(name, requester_id=2, duration=180):
    return Track(title=name, source=f"/music/{name}.mp3", duration=duration, requester=Requester(requester_id, "user"), local=True)


def default_playlist(world, tmp_path, *names):
    for name in names:
        (tmp_path / "music" / name).write_bytes(b"")
    world.bot.playlists.folder.mkdir(parents=True, exist_ok=True)
    (world.bot.playlists.folder / "auto.txt").write_text("\n".join(names), encoding="utf-8")
    world.bot.settings.update(GUILD_ID, default_playlist="auto", voice_channel_id=VOICE_CHANNEL_ID)


# Startup autoplay


def test_autostart_joins_the_setvc_channel_and_plays(make_world, tmp_path):
    async def scenario():
        world = await make_world()
        default_playlist(world, tmp_path, "x.mp3", "y.mp3")
        await world.bot.players.autostart(world.guild)
        player = world.bot.players.get(GUILD_ID)
        await wait_until(lambda: player.current is not None)
        assert world.voice_client.channel is world.voice
        assert player.current.requester is None

    run(scenario())


@pytest.mark.parametrize("missing", ["voice channel", "playlist setting", "playlist file"])
def test_autostart_needs_a_voice_channel_and_an_existing_playlist(make_world, tmp_path, missing):
    async def scenario():
        world = await make_world()
        default_playlist(world, tmp_path, "x.mp3")
        if missing == "voice channel":
            world.bot.settings.update(GUILD_ID, voice_channel_id=None)
        elif missing == "playlist setting":
            world.bot.settings.update(GUILD_ID, default_playlist=None)
        else:
            (world.bot.playlists.folder / "auto.txt").unlink()
        await world.bot.players.autostart(world.guild)
        assert world.voice_client is None

    run(scenario())


def test_autostart_leaves_a_playing_server_alone(make_world, tmp_path):
    async def scenario():
        world = await make_world()
        default_playlist(world, tmp_path, "x.mp3")
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.other_voice)
        await player.add(track("request"))
        await world.bot.players.autostart(world.guild)
        assert player.current.title == "request" and world.voice_client.channel is world.other_voice

    run(scenario())


# Leave when alone


def test_stops_after_being_alone_long_enough(make_world, monkeypatch):
    async def scenario():
        world = await make_world("[player]\nalone_time_until_stop = 30\n")
        manager = world.bot.players
        clock = [100.0]
        monkeypatch.setattr(player_module.time, "monotonic", lambda: clock[0])
        listener = world.user(2)
        player = manager.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))

        manager._check_alone(world.guild)
        assert GUILD_ID not in manager._alone_since

        world.voice.members.remove(listener)
        world.user(3, deafened=True)  # a deafened member doesn't count as listening
        manager._check_alone(world.guild)
        assert manager._alone_since[GUILD_ID] == 100.0

        clock[0] = 120.0
        await manager._stop_when_alone.coro(manager)
        assert player.current is not None  # not yet: 20 of 30 seconds

        clock[0] = 131.0
        await manager._stop_when_alone.coro(manager)
        await settle()
        assert player.current is None and world.voice_client is None

    run(scenario())


def test_someone_coming_back_cancels_the_timer(make_world, monkeypatch):
    async def scenario():
        world = await make_world("[player]\nalone_time_until_stop = 30\n")
        manager = world.bot.players
        player = manager.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))
        manager._check_alone(world.guild)
        assert GUILD_ID in manager._alone_since
        world.user(2)  # joins the channel
        manager._check_alone(world.guild)
        assert GUILD_ID not in manager._alone_since

    run(scenario())


# Kicked from voice


def test_queue_is_cleared_when_the_bot_is_disconnected_by_someone_else(make_world, monkeypatch):
    async def scenario():
        world = await make_world()
        monkeypatch.setattr(player_module.asyncio, "sleep", _no_sleep)
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))
        await player.add(track("b"))
        world.voice_client.disconnected = True  # e.g. a moderator disconnected the bot
        world.guild.voice_client = None
        bot_member = SimpleNamespace(id=BOT_ID, guild=world.guild)
        await world.bot.players.on_voice_state_update(bot_member, None, SimpleNamespace(channel=None))
        assert player.current is None and not player.queue

    run(scenario())


async def _no_sleep(seconds):
    return None


def test_seek_while_paused_stays_paused(make_world):
    async def scenario():
        world = await make_world()
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))
        player.pause()
        await player.seek(42)
        assert player.paused and player.position == 42

    run(scenario())


# Now playing


def test_now_playing_message(make_world):
    async def scenario():
        world = await make_world()
        player = world.bot.players.get(GUILD_ID)
        content, embed = now_playing(world.bot, player)
        assert embed.title == "No music playing"

        await player.connect(world.voice)
        await player.add(track("Song", duration=200))
        player.audio.position = 100
        content, embed = now_playing(world.bot, player)
        assert f"Now Playing in <#{VOICE_CHANNEL_ID}>" in content
        assert embed.title == "Song" and embed.author.name == "user"
        assert "▶" in embed.description and "`[01:40/03:20]`" in embed.description
        assert "▬▬▬▬▬▬🔘" in embed.description  # knob halfway along
        player.pause()
        assert "⏸" in now_playing(world.bot, player)[1].description

    run(scenario())


# Shutdown


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_sigterm_closes_the_bot_once():
    async def scenario():
        closes = []

        async def close():
            closes.append(1)

        loop = asyncio.get_running_loop()
        shutdown.install(loop, close)
        try:
            os.kill(os.getpid(), signal.SIGTERM)
            os.kill(os.getpid(), signal.SIGTERM)  # a second signal doesn't close twice
            await settle()
        finally:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)
        return closes

    started = []
    original = shutdown._start_watchdog
    shutdown._start_watchdog = lambda loop: started.append(loop)
    try:
        assert run(scenario()) == [1]
        assert len(started) == 1
    finally:
        shutdown._start_watchdog = original


def test_bot_close_halts_players_first(make_world):
    async def scenario():
        world = await make_world()
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))
        await player.add(track("b"))
        world.bot.players.shutdown()
        world.bot.shutting_down = True
        world.voice_client.finish()  # discord.py leaving voice ends the song
        await settle()
        assert player.current is None and not player.queue

    run(scenario())
