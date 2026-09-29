"""A queued track and who asked for it."""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class Requester:
    id: int
    name: str
    avatar_url: str | None = None


@dataclass(eq=False)  # tracks compare by identity: the same song can be queued twice
class Track:
    title: str
    # A page URL for online tracks (the stream URL expires, so it's fetched at play time),
    # or an absolute file path for local ones.
    source: str
    duration: float | None  # seconds; None for live streams
    requester: Requester | None = None  # None when the bot queued it itself
    local: bool = False
    uploader: str | None = None
    thumbnail: str | None = None
    start_offset: float = 0.0  # seconds, e.g. from a YouTube link with ?t=
    # Cached stream details for online tracks, filled in shortly before playing.
    stream_url: str | None = field(default=None, repr=False)
    stream_user_agent: str | None = field(default=None, repr=False)
    stream_fetched_at: float = field(default=0.0, repr=False)

    @property
    def requester_id(self) -> int:
        return self.requester.id if self.requester else 0

    @property
    def url(self) -> str | None:
        """The link to show for this track, if it has one."""
        return None if self.local else self.source

    @property
    def seekable(self) -> bool:
        return self.duration is not None

    def fresh_copy(self) -> Track:
        """A copy to play again from the start (repeat mode), without cached stream details."""
        return replace(self, start_offset=0.0, stream_url=None, stream_user_agent=None, stream_fetched_at=0.0)
