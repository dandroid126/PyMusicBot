"""Stand-ins for Discord objects, so commands can run against the real bot without a network.

Members, channels and servers are Mocks with a discord.py spec, so the bot's isinstance checks
accept them. The voice connection is a real VoiceClient subclass that records what it's asked to
play. Commands are run through `invoke`, which applies their checks and error handling like the
real command tree does.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from discord import app_commands

from pymusicbot.bot import EXTENSIONS, MusicBot
from pymusicbot.config import load_config
from pymusicbot.settings import SettingsStore

BOT_ID = 1000
OWNER_ID = 1
GUILD_ID = 500
TEXT_CHANNEL_ID = 600
OTHER_TEXT_CHANNEL_ID = 601
VOICE_CHANNEL_ID = 700
OTHER_VOICE_CHANNEL_ID = 701
AFK_CHANNEL_ID = 702
DJ_ROLE_ID = 800


class FakeVoice(discord.VoiceClient):
    """A voice connection that records playback instead of sending audio."""

    source = None  # shadows VoiceClient.source; the player swaps sources when seeking

    def __init__(self, channel: Mock, guild: Mock):
        self.channel = channel
        self.fake_guild = guild
        self.after = None
        self.paused = False
        self.disconnected = False

    def is_connected(self) -> bool:
        return not self.disconnected

    def is_paused(self) -> bool:
        return self.paused

    def is_playing(self) -> bool:
        return self.after is not None

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def play(self, source, *, after=None, **kwargs) -> None:
        self.source, self.after, self.paused = source, after, False

    def stop(self) -> None:
        self.finish()

    def finish(self, error: Exception | None = None) -> None:
        """End the current song, as if it played to the end."""
        after, self.after = self.after, None
        if after:
            after(error)

    async def disconnect(self, *, force: bool = False) -> None:
        self.disconnected = True
        self.fake_guild.voice_client = None
        self.finish()

    async def move_to(self, channel, **kwargs) -> None:
        self.channel = channel


def role(role_id: int) -> Mock:
    r = Mock(spec=discord.Role)
    r.id, r.mention = role_id, f"<@&{role_id}>"
    return r


def member(
    member_id: int,
    *,
    voice: Mock | None = None,
    manage_guild: bool = False,
    roles: tuple[int, ...] = (),
    deafened: bool = False,
    is_bot: bool = False,
) -> Mock:
    m = Mock(spec=discord.Member)
    m.id, m.bot, m.mention = member_id, is_bot, f"<@{member_id}>"
    m.name = m.display_name = f"user{member_id}"
    m.display_avatar = SimpleNamespace(url=f"https://cdn.example/{member_id}.png")
    m.guild_permissions = SimpleNamespace(manage_guild=manage_guild)
    m.roles = [role(r) for r in roles]
    m.voice = SimpleNamespace(channel=voice, deaf=False, self_deaf=deafened) if voice is not None else None
    return m


def voice_channel(channel_id: int, guild: Mock, *, can_connect: bool = True) -> Mock:
    c = Mock(spec=discord.VoiceChannel)
    c.id, c.mention, c.guild, c.members = channel_id, f"<#{channel_id}>", guild, []
    c.name = f"voice{channel_id}"
    c.permissions_for = Mock(return_value=SimpleNamespace(connect=can_connect, speak=can_connect))

    async def connect(**kwargs):
        guild.voice_client = FakeVoice(c, guild)
        return guild.voice_client

    c.connect = AsyncMock(side_effect=connect)
    return c


def text_channel(channel_id: int) -> Mock:
    c = Mock(spec=discord.TextChannel)
    c.id, c.mention = channel_id, f"<#{channel_id}>"
    return c


class World:
    """One server with a bot, a text channel, two voice channels and an AFK channel."""

    def __init__(self, bot: MusicBot):
        self.bot = bot
        guild = self.guild = Mock(spec=discord.Guild)
        guild.id, guild.name, guild.voice_client = GUILD_ID, "Test Server", None
        guild.me = member(BOT_ID, is_bot=True)
        guild.me.color = discord.Color.default()
        self.voice = voice_channel(VOICE_CHANNEL_ID, guild)
        self.other_voice = voice_channel(OTHER_VOICE_CHANNEL_ID, guild)
        self.afk = voice_channel(AFK_CHANNEL_ID, guild)
        guild.afk_channel = self.afk
        self.channels = {c.id: c for c in (self.voice, self.other_voice, self.afk)}
        self.channels[TEXT_CHANNEL_ID] = text_channel(TEXT_CHANNEL_ID)
        self.channels[OTHER_TEXT_CHANNEL_ID] = text_channel(OTHER_TEXT_CHANNEL_ID)
        guild.get_channel = lambda channel_id: self.channels.get(channel_id)
        guild.get_role = lambda role_id: role(role_id) if role_id else None
        bot.get_guild = lambda guild_id: guild if guild_id == GUILD_ID else None
        bot.get_channel = lambda channel_id: self.channels.get(channel_id)
        bot.change_presence = AsyncMock()

    @property
    def voice_client(self) -> FakeVoice | None:
        return self.guild.voice_client

    def user(self, member_id: int = 2, *, in_voice: str | None = "voice", **kwargs) -> Mock:
        channel = {"voice": self.voice, "other": self.other_voice, "afk": self.afk, None: None}[in_voice]
        m = member(member_id, voice=channel, **kwargs)
        m.guild = self.guild
        if channel is not None:
            channel.members.append(m)
        return m

    def interaction(self, user: Mock, *, channel_id: int = TEXT_CHANNEL_ID, **namespace) -> FakeInteraction:
        return FakeInteraction(self.bot, user, self.guild, channel_id, namespace)

    async def invoke(self, path: str, user: Mock, *, channel_id: int = TEXT_CHANNEL_ID, **options) -> FakeInteraction:
        interaction = self.interaction(user, channel_id=channel_id, **options)
        await invoke(self.bot, path, interaction, **options)
        return interaction


class FakeMessage:
    def __init__(self, record: dict):
        self.record = record
        self.guild = SimpleNamespace(id=GUILD_ID)
        self.edits: list[dict] = []

    async def edit(self, **kwargs) -> None:
        self.edits.append(kwargs)


class FakeResponse:
    def __init__(self, interaction: FakeInteraction):
        self.interaction = interaction
        self.done = False
        self.deferred = False

    def is_done(self) -> bool:
        return self.done

    async def send_message(self, content=None, **kwargs) -> None:
        assert not self.done, "responded twice"
        self.done = True
        self.interaction.sent.append({"content": content, **kwargs})

    async def defer(self, **kwargs) -> None:
        assert not self.done, "responded twice"
        self.done = self.deferred = True

    async def edit_message(self, **kwargs) -> None:
        self.done = True
        self.interaction.sent.append({"edit": True, **kwargs})


class FakeFollowup:
    def __init__(self, interaction: FakeInteraction):
        self.interaction = interaction

    async def send(self, content=None, **kwargs) -> FakeMessage:
        record = {"content": content, **kwargs}
        self.interaction.sent.append(record)
        return FakeMessage(record)


class FakeInteraction:
    def __init__(self, bot: MusicBot, user: Mock, guild: Mock, channel_id: int, namespace: dict):
        self.client = bot
        self.user = user
        self.guild = guild
        self.guild_id = guild.id
        self.channel_id = channel_id
        self.namespace = SimpleNamespace(**{k: str(v) if not isinstance(v, str) else v for k, v in namespace.items()})
        self.command = None
        self.sent: list[dict] = []
        self.response = FakeResponse(self)
        self.followup = FakeFollowup(self)

    async def original_response(self) -> FakeMessage:
        return FakeMessage(self.sent[0] if self.sent else {})

    async def edit_original_response(self, **kwargs) -> None:
        self.sent.append({"edit": True, **kwargs})

    @property
    def texts(self) -> list[str]:
        """Every message content sent or edited, in order."""
        return [r["content"] for r in self.sent if r.get("content")]

    @property
    def last(self) -> str:
        return self.texts[-1] if self.texts else ""

    @property
    def ephemeral(self) -> bool:
        return bool(self.sent and self.sent[-1].get("ephemeral"))


def find_command(bot: MusicBot, path: str) -> app_commands.Command:
    name, _, sub = path.partition(" ")
    command = bot.tree.get_command(name)
    return command.get_command(sub) if sub else command


async def invoke(bot: MusicBot, path: str, interaction: FakeInteraction, **options) -> None:
    """Run a slash command: its checks, then its callback, with the bot's error handling."""
    command = find_command(bot, path)
    interaction.command = command
    try:
        if not await command._check_can_run(interaction):
            raise app_commands.CheckFailure()
        await command.callback(command.binding, interaction, **options)
    except app_commands.AppCommandError as error:
        await bot.on_app_command_error(interaction, error)


async def make_bot(tmp_path: Path, config_toml: str = "") -> MusicBot:
    """The real bot with every command group loaded, reading config and data from tmp_path."""
    music = tmp_path / "music"
    music.mkdir(exist_ok=True)
    if "[files]" not in config_toml:
        config_toml += f'\n[files]\nmusic_folders = ["{music.as_posix()}"]\n'
    (tmp_path / "config.toml").write_text(config_toml, encoding="utf-8")
    config = load_config({
        "DISCORD_TOKEN": "token",
        "OWNER_ID": str(OWNER_ID),
        "PYMUSICBOT_CONFIG": str(tmp_path / "config.toml"),
        "PYMUSICBOT_DATA": str(tmp_path / "data"),
    })
    bot = MusicBot(config, SettingsStore.load(config.data_dir))
    bot._connection.user = SimpleNamespace(id=BOT_ID, name="PyMusicBot")
    for extension in EXTENSIONS:
        await bot.load_extension(extension)
    return bot


def fake_audio(player, track, position):
    """Replaces GuildPlayer._make_audio: no FFmpeg, just what the player needs."""
    return SimpleNamespace(position=position, volume=player.volume / 100, cleanup=lambda: None, track=track)


async def settle() -> None:
    """Let callbacks scheduled from the fake voice connection run."""
    for _ in range(10):
        await asyncio.sleep(0)


async def wait_until(condition, timeout: float = 5.0) -> None:
    """Wait for background work (e.g. autoplay loading, which runs ffprobe) to reach a state."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() > deadline:
            raise AssertionError("timed out waiting for the condition")
        await asyncio.sleep(0.01)
