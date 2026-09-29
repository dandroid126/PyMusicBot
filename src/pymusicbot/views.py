"""Interactive message components: a picker for search results and a paged embed."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord

TIMEOUT = 60  # seconds before the buttons and menus stop responding


class OwnedView(discord.ui.View):
    """A view only the member who ran the command can use. Removes itself when it times out."""

    def __init__(self, user_id: int):
        super().__init__(timeout=TIMEOUT)
        self.user_id = user_id
        self.message: discord.Message | discord.InteractionMessage | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the person who ran the command can use this.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


class PickView(OwnedView):
    """A dropdown of choices plus Cancel. Calls on_pick with the chosen index."""

    def __init__(
        self,
        user_id: int,
        options: list[discord.SelectOption],
        on_pick: Callable[[discord.Interaction, int], Awaitable[None]],
    ):
        super().__init__(user_id)
        self.on_pick = on_pick
        options = [
            discord.SelectOption(label=o.label, description=o.description, emoji=o.emoji, value=str(i))
            for i, o in enumerate(options)
        ]
        self.select = discord.ui.Select(placeholder="Choose one", options=options)
        self.select.callback = self._picked
        self.add_item(self.select)

    async def _picked(self, interaction: discord.Interaction) -> None:
        self.stop()
        await self.on_pick(interaction, int(self.select.values[0]))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", embed=None, view=None)


class PagedView(OwnedView):
    """Previous and next buttons over a list of embeds, wrapping around at the ends."""

    def __init__(self, user_id: int, pages: list[discord.Embed], start: int = 0):
        super().__init__(user_id)
        self.pages = pages
        self.index = start % len(pages)

    @property
    def embed(self) -> discord.Embed:
        return self.pages[self.index]

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = (self.index - 1) % len(self.pages)
        await interaction.response.edit_message(embed=self.embed, view=self)

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = (self.index + 1) % len(self.pages)
        await interaction.response.edit_message(embed=self.embed, view=self)
