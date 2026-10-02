"""Admin settings, playlist and owner commands."""

import discord

from conftest import run
from fakes import DJ_ROLE_ID, GUILD_ID, OWNER_ID, TEXT_CHANNEL_ID, VOICE_CHANNEL_ID, role, wait_until
from pymusicbot.playlists import ShuffleMode
from pymusicbot.settings import QueueType


def library(tmp_path, *names):
    for name in names:
        path = tmp_path / "music" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")


def playlist_file(world, name, text):
    folder = world.bot.playlists.folder
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.txt").write_text(text, encoding="utf-8")
    return folder / f"{name}.txt"


# Admin settings


def test_settc_only_explains_discords_command_permissions(make_world):
    async def scenario():
        world = await make_world()
        admin = world.user(9, manage_guild=True)
        before = world.bot.settings.get(GUILD_ID)
        result = await world.invoke("settc", admin, channel=world.channels[TEXT_CHANNEL_ID])
        assert "Integrations" in result.last and "docs/deployment.md#who-can-use-which-commands" in result.last
        assert result.ephemeral
        assert world.bot.settings.get(GUILD_ID) == before  # changes nothing

    run(scenario())


def test_admin_commands_change_settings(make_world):
    async def scenario():
        world = await make_world()
        admin = world.user(9, manage_guild=True)
        await world.invoke("setdj", admin, role=role(DJ_ROLE_ID))
        await world.invoke("setvc", admin, channel=world.voice)
        await world.invoke("setskip", admin, percent=75)
        await world.invoke("queuetype", admin, queue_type=QueueType.LINEAR)
        settings = world.bot.settings.get(GUILD_ID)
        assert (settings.dj_role_id, settings.voice_channel_id) == (DJ_ROLE_ID, VOICE_CHANNEL_ID)
        assert settings.skip_ratio == 0.75 and settings.queue_type == QueueType.LINEAR
        assert world.bot.players.get(GUILD_ID).queue.fair is False

        for path in ("setdj", "setvc", "setskip"):
            await world.invoke(path, admin)  # no option clears it
        settings = world.bot.settings.get(GUILD_ID)
        assert (settings.dj_role_id, settings.voice_channel_id, settings.skip_ratio) == (None, None, None)

        shown = await world.invoke("settings", world.user(2))
        fields = {f.name: f.value for f in shown.sent[-1]["embed"].fields}
        assert fields["Skip ratio"] == "55% (default)" and fields["Queue type"] == "Linear"

    run(scenario())


def test_setvc_starts_the_default_playlist_when_idle(make_world, tmp_path):
    async def scenario():
        world = await make_world()
        library(tmp_path, "x.mp3")
        playlist_file(world, "auto", "x.mp3\n")
        world.bot.settings.update(GUILD_ID, default_playlist="auto")
        await world.invoke("setvc", world.user(9, manage_guild=True), channel=world.voice)
        player = world.bot.players.get(GUILD_ID)
        await wait_until(lambda: player.current is not None)
        assert world.voice_client.channel is world.voice

    run(scenario())


# Playlists


def test_playlist_create_append_remove_shuffle_delete(make_world, tmp_path):
    async def scenario():
        world = await make_world()
        owner = world.user(OWNER_ID)
        library(tmp_path, "Band/Album/b.mp3", "Band/Album/a.mp3", "Band/single.mp3")

        created = await world.invoke("playlist create", owner, name="road trip")
        assert "created playlist `road_trip`" in created.last

        refused = await world.invoke("playlist append", owner, name="road_trip", entries="BandAlbum | search:lofi")
        assert "Nothing was added" in refused.last and "Did you mean `Band/Album/`?" in refused.last

        added = await world.invoke("playlist append", owner, name="road_trip", entries="Band/Album/ | Band/single.mp3 | search:lofi")
        assert "Added 4 entries" in added.last
        again = await world.invoke("playlist append", owner, name="road_trip", entries="Band/Album/a.mp3 | https://youtu.be/x")
        assert "Added 1 entry" in again.last and "Skipped 1 already in it" in again.last
        assert world.bot.playlists.entries("road_trip") == [
            "Band/Album/a.mp3", "Band/Album/b.mp3", "Band/single.mp3", "search:lofi", "https://youtu.be/x",
        ]

        removed = await world.invoke("playlist remove", owner, name="road_trip", entry="4")
        assert "Removed `search:lofi`" in removed.last

        await world.invoke("playlist shuffle", owner, name="road_trip", mode=_choice(ShuffleMode.RANDOM))
        shown = await world.invoke("playlist show", world.user(2), name="road_trip")
        assert "random: each song picked at random" in shown.sent[-1]["embed"].title

        listed = await world.invoke("playlist list", world.user(2))
        assert "`road_trip`" in listed.last

        deleted = await world.invoke("playlist delete", owner, name="road_trip")
        assert "deleted playlist `road_trip`" in deleted.last
        assert world.bot.playlists.names() == []

    run(scenario())


def _choice(mode: ShuffleMode) -> discord.app_commands.Choice:
    return discord.app_commands.Choice(name=mode.value, value=mode.value)


def test_playlist_play_in_order_and_shuffled(make_world, tmp_path):
    async def scenario():
        world = await make_world()
        names = [f"{i:02}.mp3" for i in range(12)]
        library(tmp_path, *names)
        playlist_file(world, "ordered", "\n".join(names))
        playlist_file(world, "mixed", "#shuffle\n" + "\n".join(names))
        user = world.user()

        result = await world.invoke("playlist play", user, name="ordered")
        assert "Loaded **12** tracks from **ordered**" in result.last
        player = world.bot.players.get(GUILD_ID)
        assert [player.current.title] + [t.title for t in player.queue] == [n[:-4] for n in names]

        await player.stop()
        await world.invoke("playlist play", user, name="mixed")
        order = [player.current.title] + [t.title for t in player.queue]
        assert sorted(order) == [n[:-4] for n in names] and order != [n[:-4] for n in names]

        missing = await world.invoke("playlist play", user, name="nope")
        assert "could not find `nope.txt`" in missing.last

    run(scenario())


def test_playlist_play_lists_entries_that_failed(make_world, tmp_path):
    async def scenario():
        world = await make_world()
        library(tmp_path, "ok.mp3")
        playlist_file(world, "mix", "ok.mp3\n/old/machine/gone.mp3\n")
        result = await world.invoke("playlist play", world.user(), name="mix")
        assert "Loaded **1** tracks" in result.last
        assert "`[2]` **/old/machine/gone.mp3**" in result.last and "isn't in the music library" in result.last

    run(scenario())


def test_autoplaylist_set_starts_and_clear_stops(make_world, tmp_path):
    async def scenario():
        world = await make_world()
        library(tmp_path, "x.mp3", "y.mp3")
        playlist_file(world, "auto", "x.mp3\ny.mp3\n")
        world.bot.settings.update(GUILD_ID, voice_channel_id=VOICE_CHANNEL_ID)
        owner = world.user(OWNER_ID)

        missing = await world.invoke("autoplaylist", owner, name="nope")
        assert "Could not find `nope.txt`" in missing.last

        await world.invoke("autoplaylist", owner, name="auto")
        player = world.bot.players.get(GUILD_ID)
        await wait_until(lambda: player.current is not None)  # not in voice, so it joined and started
        assert player.current.title == "x"

        cleared = await world.invoke("autoplaylist", owner)
        assert "Cleared the default playlist" in cleared.last
        assert player.autoplay == []  # nothing more lined up after the current song
        assert world.bot.settings.get(GUILD_ID).default_playlist is None

    run(scenario())


# Owner


def test_setgame_setstatus_and_debug(make_world):
    async def scenario():
        world = await make_world()
        owner = world.user(OWNER_ID)
        await world.invoke("setgame", owner, activity="Watching the queue")
        assert world.bot.base_activity.type == discord.ActivityType.watching
        assert world.bot.base_activity.name == "the queue"
        await world.invoke("setgame", owner)
        assert world.bot.base_activity is None
        await world.invoke("setstatus", owner, status="idle")
        assert world.bot.base_status == discord.Status.idle
        world.bot.change_presence.assert_awaited()

        debug = await world.invoke("debug", owner)
        record = debug.sent[-1]
        assert record["ephemeral"] and record["file"].filename == "debug_information.txt"
        text = record["file"].fp.read().decode()
        assert "discord.py = " in text and "token" not in text.lower()

    run(scenario())
