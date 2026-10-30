"""Health reports to a push monitor (Uptime Kuma): off unless configured, and what counts as down."""

from types import SimpleNamespace

import pytest

from conftest import run
from fakes import GUILD_ID, VOICE_CHANNEL_ID, settle
from pymusicbot import health as health_module
from pymusicbot.audio.track import Requester, Track
from pymusicbot.bot import MusicBot
from pymusicbot.config import ConfigError, load_config
from pymusicbot.health import GRACE_SECONDS, HealthPush, format_duration, push_base_url

KUMA = "https://kuma.example/api/push/s3cretToken?status=up&msg=OK&ping="


def track(name):
    return Track(title=name, source=f"/music/{name}.mp3", duration=180, requester=Requester(2, "user"), local=True)


def env_for(tmp_path, **extra):
    return {"DISCORD_TOKEN": "x", "PYMUSICBOT_CONFIG": str(tmp_path / "none.toml"), "PYMUSICBOT_DATA": str(tmp_path), **extra}


# Configuration


def test_off_unless_the_url_is_set(make_world, tmp_path):
    assert load_config(env_for(tmp_path)).health_push_url is None
    assert load_config(env_for(tmp_path, HEALTH_PUSH_URL="  ")).health_push_url is None

    async def scenario():
        world = await make_world()
        assert world.bot.health is None  # nothing created: no task, no listeners, no HTTP session
        assert "on_disconnect" not in world.bot.extra_events

    run(scenario())


def test_the_url_is_read_and_kept_out_of_logs(tmp_path):
    config = load_config(env_for(tmp_path, HEALTH_PUSH_URL=KUMA))
    assert config.health_push_url == KUMA
    assert "s3cretToken" not in repr(config)
    with pytest.raises(ConfigError, match="HEALTH_PUSH_URL must be a web address"):
        load_config(env_for(tmp_path, HEALTH_PUSH_URL="kuma.example/api/push/x"))


def test_kumas_example_query_is_dropped():
    assert push_base_url(KUMA) == "https://kuma.example/api/push/s3cretToken"


# What counts as healthy


@pytest.fixture
def monitored(make_world, monkeypatch):
    """A world whose bot reports health, connected to Discord, with a controllable clock."""
    clock = [1000.0]
    monkeypatch.setattr(health_module.time, "monotonic", lambda: clock[0])

    async def factory(*, radio=True):
        world = await make_world()
        monkeypatch.setattr(MusicBot, "guilds", property(lambda bot: [world.guild]))
        monkeypatch.setattr(world.bot, "is_ready", lambda: True)
        if radio:
            world.bot.playlists.folder.mkdir(parents=True, exist_ok=True)
            (world.bot.playlists.folder / "auto.txt").write_text("x.mp3", encoding="utf-8")
            world.bot.settings.update(GUILD_ID, default_playlist="auto", voice_channel_id=VOICE_CHANNEL_ID)
        world.health = HealthPush(world.bot, KUMA)
        world.clock = clock
        return world

    return factory


def later(world, seconds=GRACE_SECONDS):
    world.clock[0] += seconds


def test_playing_is_healthy(monitored):
    async def scenario():
        world = await monitored()
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))
        assert world.health.problems() == []
        later(world)
        assert world.health.problems() == []

    run(scenario())


def test_a_radio_server_that_isnt_playing_is_down_after_the_grace_period(monitored):
    """What would have caught the soak test failure: in voice, queue cleared, silent."""
    async def scenario():
        world = await monitored()
        assert world.health.problems() == []  # short gaps (next song, reconnects) are fine
        later(world, GRACE_SECONDS - 1)
        assert world.health.problems() == []
        later(world, 1)
        assert world.health.problems() == ["Not playing in Test Server for 2 min"]

    run(scenario())


def test_lost_voice_while_playing_is_down(monitored):
    async def scenario():
        world = await monitored(radio=False)
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))
        world.voice_client.forget()  # and somehow not recovered
        world.health.problems()
        later(world)
        assert world.health.problems() == ["Not playing in Test Server for 2 min"]

    run(scenario())


def test_a_failed_reconnect_that_cant_rejoin_is_down(monitored):
    """DJ_Nina, 2026-10-30: silent for hours while reporting up."""
    async def scenario():
        world = await monitored()
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))
        await world.voice_client.disconnect()  # discord.py's cleanup after the failed reconnect
        await settle()
        member = SimpleNamespace(id=world.bot.user.id, guild=world.guild)
        await world.bot.players.on_voice_state_update(member, None, SimpleNamespace(channel=None))
        world.health.problems()
        later(world)
        assert world.health.problems() == ["Not playing in Test Server for 2 min"]

    run(scenario())


def test_being_disconnected_by_someone_is_healthy(monitored):
    async def scenario():
        world = await monitored()
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))
        await world.voice_client.kick()
        await settle()
        member = SimpleNamespace(id=world.bot.user.id, guild=world.guild)
        await world.bot.players.on_voice_state_update(member, None, SimpleNamespace(channel=None))
        later(world)
        assert world.health.problems() == []

    run(scenario())


def test_stopping_on_purpose_is_healthy(monitored):
    async def scenario():
        world = await monitored()
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))
        await player.stop()
        later(world)
        assert world.health.problems() == []

    run(scenario())


def test_no_radio_and_nothing_playing_is_healthy(monitored):
    async def scenario():
        world = await monitored(radio=False)
        later(world)
        assert world.health.problems() == []

    run(scenario())


def test_not_connected_to_discord_is_down(monitored):
    async def scenario():
        world = await monitored(radio=False)
        await world.health._on_disconnect()
        later(world)
        assert world.health.problems() == ["Not connected to Discord for 2 min"]
        await world.health._on_connected()
        assert world.health.problems() == []

    run(scenario())


# What "up" reports say


@pytest.mark.parametrize(
    "seconds, text",
    [(0, "0 min"), (119, "1 min"), (3600, "1 h 0 min"), (11520, "3 h 12 min"), (183600, "2 d 3 h")],
)
def test_durations(seconds, text):
    assert format_duration(seconds) == text


def test_status_says_playing(monitored):
    async def scenario():
        world = await monitored()
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))
        assert world.health.status() == "Playing in Test Server"

    run(scenario())


def test_status_says_how_long_its_been_idle(monitored):
    async def scenario():
        world = await monitored(radio=False)
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))
        assert world.health.status() == "Playing in Test Server"
        await player.stop()
        assert world.health.status() == "Idle for 0 min"
        later(world, 720)
        assert world.health.status() == "Idle for 12 min"

    run(scenario())


def test_status_says_when_someone_disconnected_the_bot_until_it_plays_again(monitored):
    async def scenario():
        world = await monitored()
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))
        await world.voice_client.kick()
        await settle()
        member = SimpleNamespace(id=world.bot.user.id, guild=world.guild)
        await world.bot.players.on_voice_state_update(member, None, SimpleNamespace(channel=None))
        later(world, 11520)
        assert world.health.status() == "Disconnected by someone in Test Server 3 h 12 min ago"

        await player.connect(world.voice)
        await player.add(track("b"))
        assert world.health.status() == "Playing in Test Server"

    run(scenario())


# Reporting


class FakeSession:
    def __init__(self, status=200):
        self.calls, self.status = [], status

    def get(self, url, params):
        self.calls.append((url, params))
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise health_module.aiohttp.ClientResponseError(None, (), status=self.status)


def test_pushes_up_or_down_with_the_reason(monitored, caplog):
    async def scenario():
        world = await monitored()
        session = world.health._session = FakeSession()
        player = world.bot.players.get(GUILD_ID)
        await player.connect(world.voice)
        await player.add(track("a"))
        await world.health.push()
        await player.disconnect()
        await settle()
        world.health.problems()
        later(world)
        await world.health.push()
        (url, up), (_, down) = session.calls
        assert url == "https://kuma.example/api/push/s3cretToken"
        assert up["status"] == "up" and up["msg"] == "Playing in Test Server"
        assert down == {**down, "status": "down", "msg": "Not playing in Test Server for 2 min"}

        session.status = 404
        await world.health.push()
        await world.health.push()
        await settle()
        warnings = [r.getMessage() for r in caplog.records if "push monitor" in r.getMessage()]
        assert warnings == ["Couldn't reach the push monitor: HTTP 404"]  # once, not every minute
        assert "s3cretToken" not in caplog.text

    run(scenario())
