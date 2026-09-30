"""Music and DJ command behavior, run against the real bot with fake Discord objects."""

from conftest import run
from fakes import DJ_ROLE_ID, GUILD_ID, VOICE_CHANNEL_ID, settle, wait_until
from pymusicbot.audio.track import Requester, Track
from pymusicbot.settings import QueueType, RepeatMode


def track(name: str, requester_id: int | None = 2, duration: float | None = 180) -> Track:
    requester = Requester(requester_id, f"user{requester_id}") if requester_id else None
    return Track(title=name, source=f"/music/{name}.mp3", duration=duration, requester=requester, local=True)


async def playing(world, *tracks: Track):
    """Connect to the main voice channel and queue tracks; the first starts playing."""
    player = world.bot.players.get(GUILD_ID)
    await player.connect(world.voice)
    for t in tracks:
        await player.add(t)
    return player


def titles(player) -> list[str]:
    return [t.title for t in player.queue]


# /play


def test_play_queues_files_and_folders_from_the_library(make_world, tmp_path):
    async def scenario():
        world = await make_world()
        album = tmp_path / "music" / "Album"
        album.mkdir()
        for name in ("01 One.mp3", "02 Two.mp3"):
            (album / name).write_bytes(b"")
        (tmp_path / "music" / "single.mp3").write_bytes(b"")
        user = world.user()

        first = await world.invoke("play", user, query="single.mp3")
        assert "Added **single** (`LIVE`) to begin playing" in first.last  # no tags or duration in an empty file
        second = await world.invoke("play", user, query="Album")
        assert "Found **Album** with `2` entries" in second.last
        player = world.bot.players.get(GUILD_ID)
        assert player.current.title == "single"
        assert titles(player) == ["01 One", "02 Two"]

    run(scenario())


def test_play_reports_paths_that_are_not_in_the_library(make_world):
    async def scenario():
        world = await make_world()
        result = await world.invoke("play", world.user(), query="/home/dan/Music/missing.mp3")
        assert "isn't in the music library" in result.last
        assert world.voice_client is None  # didn't join for nothing

    run(scenario())


def test_play_refuses_tracks_over_the_length_limit(make_world, tmp_path):
    async def scenario():
        world = await make_world("[player]\nmax_track_length = 60\n")
        world.bot.sources._local_track = _fixed_duration(world.bot.sources._local_track, 300)
        (tmp_path / "music" / "long.mp3").write_bytes(b"")
        result = await world.invoke("play", world.user(), query="long.mp3")
        assert "longer than the allowed maximum: `05:00` > `01:00`" in result.last
        assert world.bot.players.get(GUILD_ID).current is None

    run(scenario())


def _fixed_duration(local_track, seconds):
    async def with_duration(path, requester):
        t = await local_track(path, requester)
        t.duration = seconds
        return t

    return with_duration


# /play with no query


def test_play_without_a_query(make_world):
    async def scenario():
        world = await make_world()
        help_text = await world.invoke("play", world.user())
        assert "Play commands:" in help_text.last and help_text.ephemeral

        player = await playing(world, track("a"))
        player.pause()
        regular = await world.invoke("play", world.user(3))
        assert "Only DJs can unpause" in regular.last
        dj = await world.invoke("play", world.user(4, manage_guild=True))
        assert "Resumed **a**" in dj.last and not player.paused

    run(scenario())


def test_play_without_a_query_starts_the_default_playlist(make_world, tmp_path):
    async def scenario():
        world = await make_world()
        (tmp_path / "music" / "x.mp3").write_bytes(b"")
        folder = world.bot.playlists.folder
        folder.mkdir(parents=True)
        (folder / "auto.txt").write_text("x.mp3\n", encoding="utf-8")
        world.bot.settings.update(GUILD_ID, default_playlist="auto", voice_channel_id=VOICE_CHANNEL_ID)

        result = await world.invoke("play", world.user(in_voice=None))
        assert f"Starting the default playlist **auto** in <#{VOICE_CHANNEL_ID}>" in result.last
        player = world.bot.players.get(GUILD_ID)
        await wait_until(lambda: player.current is not None)
        assert player.current.title == "x"
        assert player.current.requester is None  # autoplay

    run(scenario())


# Skipping


def test_requester_skips_right_away(make_world):
    async def scenario():
        world = await make_world()
        player = await playing(world, track("a", requester_id=2), track("b"))
        result = await world.invoke("skip", world.user(2))
        await settle()
        assert "Skipped **a**" in result.last
        assert player.current.title == "b"

    run(scenario())


def test_skip_voting_needs_the_skip_ratio_of_listeners(make_world):
    async def scenario():
        world = await make_world()  # default skip ratio 0.55
        voters = [world.user(i) for i in (3, 4, 5, 6)]  # 4 listeners -> ceil(2.2) = 3 votes
        world.user(7, deafened=True)  # deafened members don't count as listeners
        player = await playing(world, track("a", requester_id=2), track("b"))

        first = await world.invoke("skip", voters[0])
        assert "You voted to skip the song `[1 votes, 3/4 needed]`" in first.last
        again = await world.invoke("skip", voters[0])
        assert "You already voted" in again.last
        await world.invoke("skip", voters[1])
        assert player.current.title == "a"
        third = await world.invoke("skip", voters[2])
        await settle()
        assert "Skipped **a** (requested by **user2**)" in third.last
        assert player.current.title == "b"

    run(scenario())


def test_skip_ratio_zero_skips_for_anyone(make_world):
    async def scenario():
        world = await make_world()
        world.bot.settings.update(GUILD_ID, skip_ratio=0.0)
        player = await playing(world, track("a", requester_id=2), track("b"))
        await world.invoke("skip", world.user(3))
        await settle()
        assert player.current.title == "b"

    run(scenario())


# Removing, shuffling, seeking


def test_remove_only_your_own_songs_unless_dj(make_world):
    async def scenario():
        world = await make_world()
        player = await playing(world, track("now"), track("mine", 3), track("theirs", 4), track("mine2", 3))

        others = await world.invoke("remove", world.user(3), position="2")
        assert "cannot remove **theirs** because you didn't add it" in others.last
        own = await world.invoke("remove", world.user(3), position="1")
        assert "Removed **mine**" in own.last
        world.bot.settings.update(GUILD_ID, dj_role_id=DJ_ROLE_ID)
        dj = await world.invoke("remove", world.user(5, roles=(DJ_ROLE_ID,)), position="1")
        assert "Removed **theirs** from the queue (requested by **user4**)" in dj.last
        everything = await world.invoke("remove", world.user(3), position="all")
        assert "Removed your 1 entries" in everything.last
        assert titles(player) == []
        bad = await world.invoke("remove", world.user(3), position="9")
        assert "nothing in the queue" in bad.last

    run(scenario())


def test_shuffle_only_moves_your_own_songs(make_world):
    async def scenario():
        world = await make_world()
        world.bot.settings.update(GUILD_ID, queue_type=QueueType.LINEAR)
        player = await playing(world, track("now"), track("a1", 3), track("b1", 4), track("a2", 3), track("a3", 3))
        result = await world.invoke("shuffle", world.user(3))
        assert "shuffled your 3 entries" in result.last
        assert titles(player)[1] == "b1"
        none = await world.invoke("shuffle", world.user(6))
        assert "don't have any music in the queue" in none.last

    run(scenario())


def test_seek(make_world):
    async def scenario():
        world = await make_world()
        player = await playing(world, track("a", requester_id=2, duration=180))

        other = await world.invoke("seek", world.user(3), time="1:00")
        assert "cannot seek **a** because you didn't add it" in other.last
        invalid = await world.invoke("seek", world.user(2), time="soon")
        assert "Invalid seek" in invalid.last
        too_far = await world.invoke("seek", world.user(2), time="5:00")
        assert "Cannot seek to `05:00`" in too_far.last
        ok = await world.invoke("seek", world.user(2), time="1:30")
        assert "Seeked to `01:30/03:00`" in ok.last
        assert player.position == 90
        relative = await world.invoke("seek", world.user(2), time="-100")
        assert "Seeked to `00:00/03:00`" in relative.last  # clamped at the start

        await player.stop()
        await playing(world, track("stream", requester_id=2, duration=None))
        live = await world.invoke("seek", world.user(2), time="10")
        assert "not seekable" in live.last

    run(scenario())


# Queue and now playing


def test_queue_shows_pages_of_ten(make_world):
    async def scenario():
        world = await make_world()
        world.bot.settings.update(GUILD_ID, repeat_mode=RepeatMode.ALL)
        await playing(world, track("now"), *(track(f"song{i}", requester_id=2 + i % 3) for i in range(23)))
        result = await world.invoke("queue", world.user())
        record = result.sent[-1]
        assert "Current Queue | 23 entries" in record["content"]
        assert "🔁" in record["content"]
        assert record["embed"].footer.text == "Page 1/3"
        assert record["view"].pages[2].description.count("\n") == 2  # 3 songs on the last page

    run(scenario())


def test_nowplaying_is_refreshed_while_playing(make_world):
    async def scenario():
        world = await make_world()
        await playing(world, track("a"))
        result = await world.invoke("nowplaying", world.user())
        record = result.sent[-1]
        assert record["embed"].title == "a"
        assert f"Now Playing in <#{VOICE_CHANNEL_ID}>" in record["content"]
        assert GUILD_ID in world.bot.now_playing._messages

    run(scenario())


# DJ controls


def test_pause_volume_repeat_and_stop(make_world):
    async def scenario():
        world = await make_world()
        dj = world.user(9, manage_guild=True)
        player = await playing(world, track("a"), track("b"))

        await world.invoke("pause", dj)
        assert player.paused
        again = await world.invoke("pause", dj)
        assert "already paused" in again.last

        volume = await world.invoke("volume", dj, level=35)
        assert "Volume changed from `100` to `35`" in volume.last
        assert player.audio.volume == 0.35
        assert world.bot.settings.get(GUILD_ID).volume == 35

        await world.invoke("repeat", dj)
        assert world.bot.settings.get(GUILD_ID).repeat_mode == RepeatMode.ALL
        await world.invoke("repeat", dj)
        assert world.bot.settings.get(GUILD_ID).repeat_mode == RepeatMode.OFF
        await world.invoke("repeat", dj, mode=RepeatMode.SINGLE)
        assert world.bot.settings.get(GUILD_ID).repeat_mode == RepeatMode.SINGLE

        stop = await world.invoke("stop", dj)
        await settle()
        assert "stopped and the queue has been cleared" in stop.last
        assert player.current is None and not player.queue and world.voice_client is None

    run(scenario())


def test_skipto_movetrack_forceskip_forceremove(make_world):
    async def scenario():
        world = await make_world()
        world.bot.settings.update(GUILD_ID, queue_type=QueueType.LINEAR)
        dj = world.user(9, manage_guild=True)
        player = await playing(world, track("now"), track("a"), track("b", 3), track("c"), track("d", 3))

        moved = await world.invoke("movetrack", dj, source=4, destination=1)
        assert "Moved **d** from position `4` to `1`" in moved.last
        assert titles(player) == ["d", "a", "b", "c"]
        bad = await world.invoke("movetrack", dj, source=9, destination=1)
        assert "`9` is not a valid position" in bad.last

        removed = await world.invoke("forceremove", dj, member=world.user(3))
        assert "Removed `2` entries from <@3>" in removed.last
        assert titles(player) == ["a", "c"]

        skipped = await world.invoke("skipto", dj, position=2)
        await settle()
        assert "Skipped to **c**" in skipped.last
        assert player.current.title == "c"

        forced = await world.invoke("forceskip", dj)
        await settle()
        assert "Skipped **c** (requested by **user2**)" in forced.last
        assert player.current is None

    run(scenario())


def test_playnext_goes_to_the_front(make_world, tmp_path):
    async def scenario():
        world = await make_world()
        (tmp_path / "music" / "urgent.mp3").write_bytes(b"")
        dj = world.user(9, manage_guild=True)
        player = await playing(world, track("now"), track("a"), track("b"))
        result = await world.invoke("playnext", dj, query="urgent.mp3")
        assert "to the front of the queue" in result.last
        assert titles(player) == ["urgent", "a", "b"]

    run(scenario())
