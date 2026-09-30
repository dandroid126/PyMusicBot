"""Playlist commands: playing and viewing for everyone, managing files for the owner."""

from __future__ import annotations

import asyncio
import random

import discord
from discord import app_commands
from discord.ext import commands

from ..audio.local import LocalLibrary
from ..audio.sources import SEARCH_PREFIX, SourceError
from ..bot import MusicBot
from ..checks import Denied, owner_only
from ..playlists import PlaylistError, ShuffleMode, clean_name
from .music import music_checks, requester_of

MAX_MESSAGE = 2000
MODE_LABELS = {
    ShuffleMode.OFF: "in order",
    ShuffleMode.SHUFFLE: "shuffled: every song once before any repeats",
    ShuffleMode.RANDOM: "random: each song picked at random",
}


class Playlists(commands.Cog):
    playlist = app_commands.Group(name="playlist", description="Play, view and manage playlists", guild_only=True)

    def __init__(self, bot: MusicBot):
        self.bot = bot

    async def playlist_names(self, interaction: discord.Interaction[MusicBot], current: str) -> list[app_commands.Choice[str]]:
        text = current.lower()
        return [app_commands.Choice(name=n, value=n) for n in self.bot.playlists.names() if text in n.lower()][:25]

    async def entry_positions(self, interaction: discord.Interaction[MusicBot], current: str) -> list[app_commands.Choice[str]]:
        entries = self.bot.playlists.entries(interaction.namespace.name or "")
        text = current.lower()
        choices = [app_commands.Choice(name=_clip(f"{i}. {e}", 100), value=str(i)) for i, e in enumerate(entries, 1)]
        return [c for c in choices if text in c.name.lower()][:25]

    async def library_entries(self, interaction: discord.Interaction[MusicBot], current: str) -> list[app_commands.Choice[str]]:
        """Library suggestions for the entry being typed after the last |."""
        head, separator, last = current.rpartition("|")
        prefix = f"{head.rstrip()} | " if separator else ""
        last = last.strip()
        if last.startswith(("http://", "https://")) or last.lower().startswith(SEARCH_PREFIX):
            return []
        values = [prefix + path for path in await self.bot.library.search(last)]
        return [app_commands.Choice(name=v, value=v) for v in values if len(v) <= 100]

    @playlist.command(name="play", description="Queue every entry of a playlist")
    @app_commands.autocomplete(name=playlist_names)
    async def play(self, interaction: discord.Interaction[MusicBot], name: str) -> None:
        player, channel = music_checks(interaction, joining=True)
        playlist = self.bot.playlists.load(name)
        if playlist is None:
            raise Denied(f"I could not find `{clean_name(name)}.txt` in the Playlists folder.")
        if not playlist.items:
            raise Denied(f"Playlist `{playlist.name}` is empty.")
        await interaction.response.send_message(
            self.bot.reply("loading", f"Loading playlist **{playlist.name}**... ({len(playlist.items)} items)")
        )
        try:
            await player.connect(channel)
        except (discord.ClientException, TimeoutError):
            await interaction.edit_original_response(content=self.bot.reply("error", f"I couldn't connect to {channel.mention}."))
            return

        # In order, songs are queued as they load, so the first one starts right away. Shuffled,
        # every song is loaded first so the whole playlist can be mixed; each is queued once.
        requester = requester_of(interaction.user)
        shuffled = playlist.mode != ShuffleMode.OFF
        loaded, errors, pending = 0, [], []
        async for index, item, result in self.bot.sources.resolve_each(playlist.items, requester):
            if isinstance(result, SourceError):
                errors.append(f"`[{index + 1}]` **{discord.utils.escape_markdown(item)}**: {result}")
                continue
            if result.too_long:
                errors.append(f"`[{index + 1}]` **{discord.utils.escape_markdown(item)}**: longer than the allowed maximum")
            if shuffled:
                pending += result.tracks
            else:
                await player.add_many(result.tracks)
            loaded += len(result.tracks)
        if pending:
            random.shuffle(pending)
            await player.add_many(pending)

        if loaded:
            text = self.bot.reply("success", f"Loaded **{loaded}** tracks from **{playlist.name}**!")
        else:
            text = self.bot.reply("warning", "No tracks were loaded!")
            if player.current is None and not self.bot.config.player.stay_in_channel:
                await player.disconnect()
        if errors:
            text += "\nThe following entries failed to load:\n" + "\n".join(errors)
        if len(text) > MAX_MESSAGE:
            text = text[:MAX_MESSAGE - 6] + " (...)"
        await interaction.edit_original_response(content=text)

    @playlist.command(name="list", description="Show the available playlists")
    async def list_(self, interaction: discord.Interaction[MusicBot]) -> None:
        names = self.bot.playlists.names()
        if not names:
            await interaction.response.send_message(self.bot.reply("warning", "There are no playlists in the Playlists folder!"))
            return
        await interaction.response.send_message(
            self.bot.reply("success", "Available playlists:\n")
            + " ".join(f"`{n}`" for n in names)
            + "\nUse `/playlist play <name>` to play a playlist"
        )

    @playlist.command(name="show", description="Show the entries of a playlist")
    @app_commands.autocomplete(name=playlist_names)
    async def show(self, interaction: discord.Interaction[MusicBot], name: str) -> None:
        playlist = self.bot.playlists.load(name)
        if playlist is None:
            raise Denied(f"Playlist `{clean_name(name)}` doesn't exist!")
        # File order, so the numbers match /playlist remove even for shuffled playlists.
        entries = self.bot.playlists.entries(name)
        lines = [f"`{i + 1}.` {discord.utils.escape_markdown(item)}" for i, item in enumerate(entries)]
        description, shown = "", 0
        for line in lines:
            if len(description) + len(line) > 3800:
                break
            description += line + "\n"
            shown += 1
        if shown < len(lines):
            description += f"...and {len(lines) - shown} more"
        embed = discord.Embed(
            title=f"{playlist.name} ({len(lines)} entries, {MODE_LABELS[playlist.mode]})",
            description=description or "This playlist is empty.",
            color=interaction.guild.me.color,
        )
        await interaction.response.send_message(embed=embed)

    @playlist.command(name="create", description="Create an empty playlist (owner)")
    @owner_only
    async def create(self, interaction: discord.Interaction[MusicBot], name: str) -> None:
        created = self._run(self.bot.playlists.create, name)
        await interaction.response.send_message(self.bot.reply("success", f"Successfully created playlist `{created}`!"))

    @playlist.command(name="append", description="Add entries to a playlist (owner)")
    @app_commands.describe(
        entries="Music library paths (suggested as you type), URLs, or search:<words>, separated by |"
    )
    @app_commands.autocomplete(name=playlist_names, entries=library_entries)
    @owner_only
    async def append(self, interaction: discord.Interaction[MusicBot], name: str, entries: str) -> None:
        items = [e.strip().removeprefix("<").removesuffix(">") for e in entries.split("|")]
        items = [e for e in items if e]
        if not items:
            raise Denied("Please include entries to add, separated by `|`.")
        if self.bot.playlists.load(name) is None:
            raise Denied(f"Playlist `{clean_name(name)}` doesn't exist!")

        expanded, problems = await expand_entries(self.bot.library, items)
        if problems:
            raise Denied(
                "Nothing was added.\n" + "\n".join(problems)
                + f"\nTo add a YouTube search on purpose, start the entry with `{SEARCH_PREFIX}`."
            )

        seen = set(self.bot.playlists.entries(name))
        new = [e for e in expanded if not (e in seen or seen.add(e))]
        skipped = len(expanded) - len(new)
        if not new:
            await interaction.response.send_message(self.bot.reply("warning", "Everything you listed is already in that playlist."))
            return
        playlist = self._run(self.bot.playlists.append, name, new)
        text = f"Added {len(new)} {'entry' if len(new) == 1 else 'entries'} to playlist `{playlist}`!"
        if skipped:
            text += f" Skipped {skipped} already in it."
        await interaction.response.send_message(self.bot.reply("success", text))
        await self.bot.players.default_playlist_edited(playlist)

    @playlist.command(name="remove", description="Remove one entry from a playlist (owner)")
    @app_commands.describe(entry="The entry to remove (suggested as you type)")
    @app_commands.autocomplete(name=playlist_names, entry=entry_positions)
    @owner_only
    async def remove(self, interaction: discord.Interaction[MusicBot], name: str, entry: str) -> None:
        try:
            position = int(entry)
        except ValueError:
            raise Denied("Pick an entry from the suggestions, or give its number from `/playlist show`.") from None
        playlist, removed = self._run(self.bot.playlists.remove, name, position)
        await interaction.response.send_message(
            self.bot.reply("success", f"Removed `{discord.utils.escape_markdown(removed)}` from playlist `{playlist}`.")
        )
        await self.bot.players.default_playlist_edited(playlist)

    @playlist.command(name="shuffle", description="Set how a playlist is shuffled (owner)")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Off: play in order", value=ShuffleMode.OFF.value),
        app_commands.Choice(name="Shuffle: every song once, in random order, before any repeats", value=ShuffleMode.SHUFFLE.value),
        app_commands.Choice(name="Random: pick any song at random each time; repeats can happen", value=ShuffleMode.RANDOM.value),
    ])
    @app_commands.autocomplete(name=playlist_names)
    @owner_only
    async def shuffle(self, interaction: discord.Interaction[MusicBot], name: str, mode: app_commands.Choice[str]) -> None:
        shuffle_mode = ShuffleMode(mode.value)
        playlist = self._run(self.bot.playlists.set_mode, name, shuffle_mode)
        await interaction.response.send_message(
            self.bot.reply("success", f"Playlist `{playlist}` now plays {MODE_LABELS[shuffle_mode]}.")
        )
        await self.bot.players.default_playlist_edited(playlist)

    @playlist.command(name="delete", description="Delete a playlist (owner)")
    @app_commands.autocomplete(name=playlist_names)
    @owner_only
    async def delete(self, interaction: discord.Interaction[MusicBot], name: str) -> None:
        deleted = self._run(self.bot.playlists.delete, name)
        await interaction.response.send_message(self.bot.reply("success", f"Successfully deleted playlist `{deleted}`!"))
        await self.bot.players.default_playlist_edited(deleted)

    @app_commands.command(description="Set or clear the playlist played when the queue is empty (owner)")
    @app_commands.describe(name="A playlist name, or leave empty to clear it")
    @app_commands.autocomplete(name=playlist_names)
    @app_commands.guild_only()
    @owner_only
    async def autoplaylist(self, interaction: discord.Interaction[MusicBot], name: str | None = None) -> None:
        guild = interaction.guild
        if not name or name.strip().lower() == "none":
            self.bot.settings.update(guild.id, default_playlist=None)
            await self._reload_autoplay(guild.id)
            await interaction.response.send_message(self.bot.reply("success", f"Cleared the default playlist for **{guild.name}**"))
            return
        playlist = self.bot.playlists.load(name)
        if playlist is None:
            raise Denied(f"Could not find `{clean_name(name)}.txt`!")
        self.bot.settings.update(guild.id, default_playlist=playlist.name)
        await self._reload_autoplay(guild.id)
        await interaction.response.send_message(
            self.bot.reply("success", f"The default playlist for **{guild.name}** is now `{playlist.name}`")
        )

    async def _reload_autoplay(self, guild_id: int) -> None:
        player = self.bot.players.find(guild_id)
        if player and player.voice:
            await player.reload_autoplay()  # takes over after the current song
        elif guild := self.bot.get_guild(guild_id):
            await self.bot.players.autostart(guild)  # not in voice: join the /setvc channel and start

    @staticmethod
    def _run(action, *args):
        try:
            return action(*args)
        except PlaylistError as e:
            raise Denied(str(e)) from None
        except OSError as e:
            raise Denied(f"The playlist file couldn't be changed: {e.strerror}") from None


async def expand_entries(library: LocalLibrary, items: list[str]) -> tuple[list[str], list[str]]:
    """Check entries for a playlist. Returns (entries to write, problems).

    Library paths are written relative to the music folder, and folders are expanded into one
    line per song, so each can be removed on its own later. URLs and search: entries pass as is.
    """
    expanded, problems = [], []
    for item in items:
        if item.startswith(("http://", "https://")) or item.lower().startswith(SEARCH_PREFIX):
            expanded.append(item)
            continue
        path = library.resolve(item)
        if path is None:
            hint = await library.suggest(item)
            problems.append(f"`{item}` isn't in the music library." + (f" Did you mean `{hint}`?" if hint else ""))
        elif path.is_dir():
            files = await asyncio.to_thread(library.audio_files, path)
            if not files:
                problems.append(f"`{item}` doesn't contain any audio files.")
            expanded += [library.display_path(f) for f in files]
        else:
            expanded.append(library.display_path(path))
    return expanded, problems


def _clip(text: str, length: int) -> str:
    return text if len(text) <= length else text[:length - 1] + "…"


async def setup(bot: MusicBot) -> None:
    await bot.add_cog(Playlists(bot))
