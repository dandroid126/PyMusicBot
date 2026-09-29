import discord
import pytest

from pymusicbot.checks import has_dj_role
from pymusicbot.presence import parse_activity


@pytest.mark.parametrize(
    ("game", "kind", "name"),
    [
        ("Playing chess", discord.ActivityType.playing, "chess"),
        ("Listening to lofi", discord.ActivityType.listening, "lofi"),
        ("listening jazz", discord.ActivityType.listening, "jazz"),
        ("Watching the queue", discord.ActivityType.watching, "the queue"),
        ("DEFAULT", discord.ActivityType.listening, "/play"),
        ("just text", discord.ActivityType.playing, "just text"),
    ],
)
def test_parse_activity(game, kind, name):
    activity = parse_activity(game)
    assert activity.type == kind
    assert activity.name == name


def test_parse_activity_none_and_streaming():
    assert parse_activity("NONE") is None
    assert parse_activity("  ") is None
    streaming = parse_activity("Streaming someuser Late night set")
    assert isinstance(streaming, discord.Streaming)
    assert streaming.url == "https://twitch.tv/someuser"
    assert streaming.name == "Late night set"


def test_has_dj_role():
    assert not has_dj_role(None, 1, {5})
    assert has_dj_role(5, 1, {5, 6})
    assert not has_dj_role(7, 1, {5, 6})
    assert has_dj_role(1, 1, set())  # DJ role set to @everyone
