import asyncio

import pytest

from fakes import World, fake_audio, make_bot
from pymusicbot.audio.player import GuildPlayer


@pytest.fixture
def make_world(tmp_path, monkeypatch):
    """Returns an async factory for a World: the real bot, one fake server, no FFmpeg."""
    monkeypatch.setattr(GuildPlayer, "_make_audio", fake_audio)

    async def factory(config_toml: str = "") -> World:
        return World(await make_bot(tmp_path, config_toml))

    return factory


def run(coro):
    return asyncio.run(coro)
