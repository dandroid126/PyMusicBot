"""The interactive pieces: search and library pickers, the Load playlist button, queue pages."""

import discord

from conftest import run
from fakes import GUILD_ID, FakeInteraction
from pymusicbot.audio.sources import Resolved
from pymusicbot.audio.track import Track
from pymusicbot.views import OwnedView, PagedView


def click(world, user) -> FakeInteraction:
    """An interaction for pressing a button or picking from a menu."""
    return world.interaction(user)


def online(name, duration=200):
    return Track(title=name, source=f"https://www.youtube.com/watch?v={name}", duration=duration, uploader="Uploader")


def test_search_picker_queues_the_chosen_result(make_world):
    async def scenario():
        world = await make_world()
        results = [online("first"), online("second"), online("third")]

        async def fake_search(query, site="ytsearch", count=5):
            assert (query, site) == ("lofi", "scsearch")
            return results

        world.bot.sources.search = fake_search
        world.bot.sources.ensure_stream = _no_stream
        user = world.user(2)
        result = await world.invoke("scsearch", user, query="lofi")
        record = result.sent[-1]
        assert "SoundCloud results for `lofi`" in record["content"]
        view = record["view"]
        assert [o.label for o in view.select.options] == ["1. first", "2. second", "3. third"]

        someone_else = click(world, world.user(3))
        assert not await view.interaction_check(someone_else)
        assert "Only the person who ran the command" in someone_else.last

        pick = click(world, user)
        view.select._values = ["1"]
        await view.select.callback(pick)
        assert "Added **second**" in pick.last and "to begin playing" in pick.last
        player = world.bot.players.get(GUILD_ID)
        assert player.current.title == "second" and player.current.requester.id == 2

    run(scenario())


async def _no_stream(track):
    return None


def test_search_cancel(make_world):
    async def scenario():
        world = await make_world()

        async def fake_search(query, site="ytsearch", count=5):
            return [online("only")]

        world.bot.sources.search = fake_search
        result = await world.invoke("search", world.user(2), query="x")
        view = result.sent[-1]["view"]
        press = click(world, world.user(2))
        await view.cancel.callback(press)
        assert press.sent[-1]["content"] == "Cancelled."
        assert world.bot.players.get(GUILD_ID).current is None

    run(scenario())


def test_local_picker_plays_a_folder(make_world, tmp_path):
    async def scenario():
        world = await make_world()
        album = tmp_path / "music" / "Band" / "Album"
        album.mkdir(parents=True)
        for name in ("1.mp3", "2.mp3"):
            (album / name).write_bytes(b"")
        user = world.user(2)
        result = await world.invoke("local", user, query="album")
        view = result.sent[-1]["view"]
        labels = [o.label for o in view.select.options]
        assert labels[0] == "Band/Album/"  # folders first
        pick = click(world, user)
        view.select._values = ["0"]
        await view.select.callback(pick)
        assert "Found **Band/Album** with `2` entries" in pick.last
        nothing = await world.invoke("local", user, query="zzz")
        assert "Nothing in the music library matches" in nothing.last

    run(scenario())


def test_load_playlist_button_adds_the_rest_of_the_playlist(make_world):
    async def scenario():
        world = await make_world()
        url = "https://www.youtube.com/watch?v=b&list=PL1"
        first = online("b")
        first.source = "https://www.youtube.com/watch?v=b"
        rest = [online("a"), online("b"), online("c")]

        async def fake_resolve(query, requester):
            if query == url:
                return Resolved([first])
            assert query == "https://www.youtube.com/playlist?list=PL1"
            return Resolved(rest, playlist_title="Mix")

        world.bot.sources.resolve = fake_resolve
        world.bot.sources.ensure_stream = _no_stream
        user = world.user(2)
        result = await world.invoke("play", user, query=url)
        record = result.sent[-1]
        assert "part of a playlist" in record["content"]
        press = click(world, user)
        await record["view"].children[0].callback(press)
        assert "Loaded **2** additional tracks" in press.last  # the video already added is skipped
        player = world.bot.players.get(GUILD_ID)
        assert [t.title for t in player.queue] == ["a", "c"]

    run(scenario())


def test_queue_pages_wrap_around(make_world):
    async def scenario():
        pages = [discord.Embed(description=str(i)) for i in range(3)]
        view = PagedView(2, pages)
        world = await make_world()
        press = click(world, world.user(2))
        await view.previous.callback(press)
        assert view.index == 2 and press.sent[-1]["embed"].description == "2"
        await view.next.callback(click(world, world.user(2)))
        assert view.index == 0

    run(scenario())


def test_menus_are_removed_when_they_time_out(make_world):
    async def scenario():
        await make_world()
        view = OwnedView(2)
        edits = []

        class Message:
            async def edit(self, **kwargs):
                edits.append(kwargs)

        view.message = Message()
        await view.on_timeout()
        assert edits == [{"view": None}]

    run(scenario())
