"""Where and when music commands work: the /settc channel, voice channel rules and permissions."""

from conftest import run
from fakes import (
    DJ_ROLE_ID,
    GUILD_ID,
    OTHER_TEXT_CHANNEL_ID,
    OTHER_VOICE_CHANNEL_ID,
    OWNER_ID,
    TEXT_CHANNEL_ID,
    VOICE_CHANNEL_ID,
)


def add_song(folder, name="song.mp3"):
    (folder / name).write_bytes(b"")
    return name


def test_music_commands_only_work_in_the_settc_channel(make_world, tmp_path):
    async def scenario():
        world = await make_world()
        world.bot.settings.update(GUILD_ID, text_channel_id=TEXT_CHANNEL_ID)
        song = add_song(tmp_path / "music")
        wrong = await world.invoke("play", world.user(), channel_id=OTHER_TEXT_CHANNEL_ID, query=song)
        assert f"only use that command in <#{TEXT_CHANNEL_ID}>" in wrong.last
        assert wrong.ephemeral
        right = await world.invoke("play", world.user(), query=song)
        assert "to begin playing" in right.last

    run(scenario())


def test_play_needs_you_in_voice_without_a_setvc_channel(make_world, tmp_path):
    async def scenario():
        world = await make_world()
        song = add_song(tmp_path / "music")
        result = await world.invoke("play", world.user(in_voice=None), query=song)
        assert "must be listening in a voice channel" in result.last
        assert world.voice_client is None

    run(scenario())


def test_play_joins_the_setvc_channel_from_anywhere(make_world, tmp_path):
    async def scenario():
        world = await make_world()
        world.bot.settings.update(GUILD_ID, voice_channel_id=VOICE_CHANNEL_ID)
        song = add_song(tmp_path / "music")
        result = await world.invoke("play", world.user(in_voice=None), query=song)
        assert "to begin playing" in result.last
        assert world.voice_client.channel is world.voice

    run(scenario())


def test_listening_rules(make_world, tmp_path):
    async def scenario():
        world = await make_world()
        song = add_song(tmp_path / "music")
        await world.invoke("play", world.user(2), query=song)  # bot joins the main voice channel

        elsewhere = await world.invoke("skip", world.user(3, in_voice="other"))
        assert f"must be listening in <#{VOICE_CHANNEL_ID}>" in elsewhere.last

        deafened = await world.invoke("skip", world.user(4, deafened=True))
        assert f"must be listening in <#{VOICE_CHANNEL_ID}>" in deafened.last

        await world.bot.players.get(GUILD_ID).stop()
        afk = await world.invoke("play", world.user(5, in_voice="afk"), query=song)
        assert "AFK channel" in afk.last

    run(scenario())


def test_bot_needs_permission_to_join(make_world, tmp_path):
    async def scenario():
        world = await make_world()
        world.other_voice.permissions_for.return_value.connect = False
        result = await world.invoke("play", world.user(in_voice="other"), query=add_song(tmp_path / "music"))
        assert f"unable to connect to <#{OTHER_VOICE_CHANNEL_ID}>" in result.last

    run(scenario())


def test_commands_that_need_music_playing(make_world):
    async def scenario():
        world = await make_world()
        for command in ("skip", "queue", "seek", "shuffle", "pause", "forceskip"):
            options = {"time": "10"} if command == "seek" else {}
            result = await world.invoke(command, world.user(manage_guild=True), **options)
            assert "There must be music playing" in result.last, command

    run(scenario())


def test_dj_commands_need_the_dj_role_manage_server_or_owner(make_world):
    async def scenario():
        world = await make_world()
        regular = await world.invoke("volume", world.user(2), level=50)
        assert "Only DJs can use this command" in regular.last

        world.bot.settings.update(GUILD_ID, dj_role_id=DJ_ROLE_ID)
        for user in (world.user(3, roles=(DJ_ROLE_ID,)), world.user(4, manage_guild=True), world.user(OWNER_ID)):
            allowed = await world.invoke("volume", user, level=50)
            assert "Volume changed" in allowed.last

        world.bot.settings.update(GUILD_ID, dj_role_id=GUILD_ID)  # DJ role set to @everyone
        everyone = await world.invoke("volume", world.user(5), level=40)
        assert "Volume changed" in everyone.last

    run(scenario())


def test_admin_and_owner_commands_are_refused_for_regular_members(make_world):
    async def scenario():
        world = await make_world()
        user = world.user(2)
        for path, options in [
            ("setdj", {}), ("settc", {}), ("setvc", {}), ("setskip", {}), ("queuetype", {}),
        ]:
            result = await world.invoke(path, user, **options)
            assert "Manage Server permission" in result.last, path
        for path, options in [
            ("playlist create", {"name": "x"}), ("playlist append", {"name": "x", "entries": "y"}),
            ("playlist remove", {"name": "x", "entry": "1"}), ("playlist delete", {"name": "x"}),
            ("autoplaylist", {"name": "x"}), ("setgame", {"activity": "x"}), ("setstatus", {"status": "idle"}),
            ("setname", {"name": "abc"}), ("shutdown", {}), ("debug", {}),
        ]:
            result = await world.invoke(path, user, **options)
            assert "Only the bot owner" in result.last, path

    run(scenario())


def test_admin_commands_are_hidden_from_regular_members_in_discord(make_world):
    async def scenario():
        world = await make_world()
        for name in ("setdj", "settc", "setvc", "setskip", "queuetype"):
            command = world.bot.tree.get_command(name)
            assert command.default_permissions.manage_guild, name
            assert command.guild_only, name

    run(scenario())


def test_everyone_can_use_the_basic_commands(make_world):
    async def scenario():
        world = await make_world()
        for path in ("playlist list", "settings", "nowplaying"):
            result = await world.invoke(path, world.user(2))
            assert "Only" not in result.last and "permission" not in result.last, path

    run(scenario())


def test_every_listed_owner_can_use_owner_commands(make_world):
    async def scenario():
        world = await make_world("owner_ids = [10, 11]")
        for owner in (10, 11):
            result = await world.invoke("debug", world.user(owner))
            report = result.sent[-1]["file"].fp.read().decode()
            assert "Owners = 10, 11" in report, owner
        other = await world.invoke("debug", world.user(12))
        assert "Only the bot owner" in other.last

    run(scenario())
