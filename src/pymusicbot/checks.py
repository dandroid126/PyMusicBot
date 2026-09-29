"""Who may run which commands, matching JMusicBot's rules.

- Owner: the bot owner.
- Admin: the owner, or anyone with Manage Server.
- DJ: admins, or members with the server's DJ role. A DJ role set to @everyone lets everyone in.

The ``require_*`` functions raise Denied with a message for the user; the ``*_only``
decorators apply them to single commands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

if TYPE_CHECKING:
    from .bot import MusicBot


class Denied(app_commands.CheckFailure):
    """A check failed; the message is shown to the user."""


def has_dj_role(dj_role_id: int | None, guild_id: int, member_role_ids: set[int]) -> bool:
    if dj_role_id is None:
        return False
    return dj_role_id == guild_id or dj_role_id in member_role_ids  # @everyone's ID is the guild's


async def is_owner(interaction: discord.Interaction[MusicBot]) -> bool:
    return await interaction.client.is_owner(interaction.user)


async def is_admin(interaction: discord.Interaction[MusicBot]) -> bool:
    if await is_owner(interaction):
        return True
    member = interaction.user
    return isinstance(member, discord.Member) and member.guild_permissions.manage_guild


async def is_dj(interaction: discord.Interaction[MusicBot]) -> bool:
    if await is_admin(interaction):
        return True
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    settings = interaction.client.settings.get(member.guild.id)
    return has_dj_role(settings.dj_role_id, member.guild.id, {r.id for r in member.roles})


async def require_owner(interaction: discord.Interaction[MusicBot]) -> bool:
    if not await is_owner(interaction):
        raise Denied("Only the bot owner can use this command.")
    return True


async def require_admin(interaction: discord.Interaction[MusicBot]) -> bool:
    if not await is_admin(interaction):
        raise Denied("You need the Manage Server permission to use this command.")
    return True


async def require_dj(interaction: discord.Interaction[MusicBot]) -> bool:
    if not await is_dj(interaction):
        raise Denied("Only DJs can use this command.")
    return True


owner_only = app_commands.check(require_owner)
admin_only = app_commands.check(require_admin)
dj_only = app_commands.check(require_dj)
