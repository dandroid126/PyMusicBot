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
        await world.voice_client.disconnect()  # what discord.py does when a moderator disconnects the bot
        await settle()
        await world.bot.players.on_voice_state_update(BOT_MEMBER(world), None, SimpleNamespace(channel=None))
        assert player.current is None and not player.queue
        assert player.wanted_channel_id is None  # not rejoined
        await world.bot.players.check_voice()
        assert world.voice_client is None

    run(scenario())


async def _no_sleep(seconds):
    return None


def BOT_MEMBER(world):
    return SimpleNamespace(id=BOT_ID, guild=world.guild)


# Voice connection drops (soak test 2026-10: a slow reconnect cleared the queue and stopped playback)


def test_a_slow_voice_reconnect_keeps_everything(make_world, monkeypatch):
    async def scenario():
        world = await make_world()
        monkeypatch.setattr(player_module.asyncio, "sleep", _no_sleep)
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))
        await player.add(track("b"))
        world.voice_client.reconnecting = True  # still reconnecting after the "left voice" check
        await world.bot.players.on_voice_state_update(BOT_MEMBER(world), None, SimpleNamespace(channel=None))
        assert player.current.title == "a" and [t.title for t in player.queue] == ["b"]
        world.voice_client.reconnecting = False
        await world.bot.players.on_voice_state_update(BOT_MEMBER(world), None, SimpleNamespace(channel=None))
        assert player.current.title == "a" and [t.title for t in player.queue] == ["b"]

    run(scenario())


def test_a_song_cut_off_by_a_reconnect_resumes_where_it_stopped(make_world, monkeypatch):
    """DJ_Nina, 2026-10-06: the song was killed mid-reconnect and the queue was wiped silently."""
    async def scenario():
        world = await make_world()
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))
        await player.add(track("b"))
        player.audio.position = 42.0
        voice = world.voice_client
        voice.reconnecting = True
        voice.finish()  # discord.py's audio thread ends the song when the connection drops
        await asyncio.sleep(0.6)  # the player is waiting for the reconnect
        assert player.current is None and [t.title for t in player.queue] == ["a", "b"]
        voice.reconnecting = False
        await wait_until(lambda: player.current is not None)
        assert player.current.title == "a" and player.current.start_offset == 42.0
        assert [t.title for t in player.queue] == ["b"]

    run(scenario())


def test_a_song_starting_during_a_reconnect_waits_for_it(make_world):
    async def scenario():
        world = await make_world()
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))
        await player.add(track("b"))
        world.voice_client.reconnecting = True
        world.voice_client.after = None  # "a" ended normally just as the connection dropped
        player._end_reason = "skip"
        task = asyncio.create_task(player._on_play_end(player._play_id, None))
        await asyncio.sleep(0.6)
        assert not task.done()  # waiting, not clearing
        world.voice_client.reconnecting = False
        await task
        assert player.current.title == "b"

    run(scenario())


def test_a_replaced_discord_session_rejoins_and_carries_on(make_world):
    """Both bots, 2026-10-09: the network dropped, Discord replaced the session, voice was gone."""
    async def scenario():
        world = await make_world()
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))
        await player.add(track("b"))
        player.audio.position = 30.0
        old = world.voice_client
        old.forget()
        await world.bot.players.check_voice()  # runs right after the bot reconnects to Discord
        await settle()
        assert world.voice_client is not None and world.voice_client is not old
        assert old.cleaned_up  # the forgotten connection was shut down
        assert world.voice_client.channel is world.voice
        assert player.current.title == "a" and player.current.start_offset == 30.0
        assert [t.title for t in player.queue] == ["b"]

    run(scenario())


def test_a_stuck_reconnect_is_given_a_minute_then_rejoined(make_world, monkeypatch):
    async def scenario():
        world = await make_world()
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))
        old = world.voice_client
        old.reconnecting = True
        clock = [1000.0]
        monkeypatch.setattr(player_module.time, "monotonic", lambda: clock[0])
        await world.bot.players.check_voice()
        assert world.voice_client is old  # discord.py gets time to reconnect by itself
        clock[0] += 61
        await world.bot.players.check_voice()
        await settle()
        assert world.voice_client is not old and world.voice_client.is_connected()
        assert player.current.title == "a"

    run(scenario())


def test_recovering_isnt_mistaken_for_leaving(make_world):
    async def scenario():
        world = await make_world()
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))
        await player.add(track("b"))
        old = world.voice_client
        old.forget()
        player._recovering = True
        player.current = None  # as recover() does after putting the song back
        player.queue.add_at(0, track("a"))
        await old.disconnect()  # recover() shuts the forgotten connection down: discord.py reports leaving
        await settle()
        await world.bot.players.on_voice_state_update(BOT_MEMBER(world), None, SimpleNamespace(channel=None))
        player._recovering = False
        assert [t.title for t in player.queue] == ["a", "b"]  # not cleared

    run(scenario())


def test_recovering_keeps_the_resume_position_of_a_song_still_starting(make_world):
    async def scenario():
        world = await make_world()
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        resumed = track("a")
        resumed.start_offset = 50.0
        player.current, player.audio = resumed, None  # claimed, waiting in _start for the reconnect
        world.voice_client.forget()
        await world.bot.players.check_voice()
        await settle()
        assert player.current.title == "a" and player.current.start_offset == 50.0

    run(scenario())


def test_a_command_during_a_reconnect_waits_for_it(make_world):
    async def scenario():
        world = await make_world()
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        voice = world.voice_client
        voice.reconnecting = True
        joining = asyncio.create_task(player.connect(world.voice))
        await asyncio.sleep(0.6)
        assert not joining.done()
        voice.reconnecting = False
        await joining
        assert world.voice_client is voice  # the same connection, not a second one

    run(scenario())


def test_reconnecting_to_discord_checks_voice_right_away(make_world, monkeypatch):
    async def scenario():
        world = await make_world()
        checks, starts = [], []
        monkeypatch.setattr(world.bot.players, "check_voice", lambda: _record(checks))
        monkeypatch.setattr(world.bot.players, "autostart", lambda guild: _record(starts))
        world.bot._checked = True  # on_ready already ran once: this is a reconnect with a new session
        await world.bot.on_ready()
        assert checks and not starts  # recovers what was playing; doesn't restart a stopped radio

    run(scenario())


async def _record(calls):
    calls.append(1)


@pytest.mark.parametrize("how", ["stop", "alone", "queue finished"])
def test_no_rejoining_after_stopping_on_purpose(make_world, how):
    async def scenario():
        world = await make_world()
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))
        if how in ("stop", "alone"):  # the alone timeout calls stop() too
            await player.stop()
        else:
            world.voice_client.finish()
            await settle()
        assert world.voice_client is None and player.wanted_channel_id is None
        await world.bot.players.check_voice()
        assert world.voice_client is None

    run(scenario())


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
