"""The upcoming-tracks queue, ported from JMusicBot's FairQueue and LinearQueue.

Linear: tracks play in the order they were added.
Fair: each requester's next track goes into the next round in which they don't have one yet,
so people take turns instead of one person's 20-song playlist blocking everyone.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from typing import Generic, Protocol, TypeVar


class Queueable(Protocol):
    @property
    def requester_id(self) -> int: ...


T = TypeVar("T", bound=Queueable)


class TrackQueue(Generic[T]):
    def __init__(self, fair: bool = True):
        self.fair = fair
        self._items: list[T] = []

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[T]:
        return iter(self._items)

    def __getitem__(self, index: int) -> T:
        return self._items[index]

    def add(self, item: T) -> int:
        """Add a track and return its index."""
        if not self.fair:
            self._items.append(item)
            return len(self._items) - 1

        # Start after this requester's last track, then walk forward through the round
        # of distinct requesters and insert before anyone appears a second time.
        index = 1 + max(
            (i for i, queued in enumerate(self._items) if queued.requester_id == item.requester_id), default=-1
        )
        seen: set[int] = set()
        while index < len(self._items):
            requester = self._items[index].requester_id
            if requester in seen:
                break
            seen.add(requester)
            index += 1
        self._items.insert(index, item)
        return index

    def add_at(self, index: int, item: T) -> None:
        self._items.insert(min(index, len(self._items)), item)

    def pull(self) -> T:
        return self._items.pop(0)

    def remove(self, index: int) -> T:
        return self._items.pop(index)

    def remove_all(self, requester_id: int) -> int:
        before = len(self._items)
        self._items = [item for item in self._items if item.requester_id != requester_id]
        return before - len(self._items)

    def clear(self) -> None:
        self._items.clear()

    def shuffle(self, requester_id: int, rng: random.Random | None = None) -> int:
        """Shuffle one requester's tracks among the positions they already hold."""
        positions = [i for i, item in enumerate(self._items) if item.requester_id == requester_id]
        tracks = [self._items[i] for i in positions]
        (rng or random).shuffle(tracks)
        for position, track in zip(positions, tracks):
            self._items[position] = track
        return len(positions)

    def skip(self, count: int) -> None:
        """Drop the first `count` tracks."""
        del self._items[:max(count, 0)]

    def move(self, source: int, destination: int) -> T:
        item = self._items.pop(source)
        self._items.insert(destination, item)
        return item
