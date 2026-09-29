"""Includes the tests ported from JMusicBot's FairQueueTest."""

import random
from dataclasses import dataclass

from pymusicbot.audio.queue import TrackQueue


@dataclass(eq=False)
class Q:
    requester_id: int
    name: str = ""


def names(queue):
    return [item.name for item in queue]


def test_different_identifier_size():
    queue = TrackQueue(fair=True)
    for i in range(100):
        queue.add(Q(i))
    assert len(queue) == 100


def test_same_identifier_size():
    queue = TrackQueue(fair=True)
    for _ in range(100):
        queue.add(Q(0))
    assert len(queue) == 100


def test_fair_queue_takes_turns():
    queue = TrackQueue(fair=True)
    for name in ("a1", "a2", "a3"):
        queue.add(Q(1, name))
    assert queue.add(Q(2, "b1")) == 1
    assert queue.add(Q(2, "b2")) == 3
    queue.add(Q(3, "c1"))
    assert names(queue) == ["a1", "b1", "c1", "a2", "b2", "a3"]


def test_linear_queue_appends():
    queue = TrackQueue(fair=False)
    for requester, name in ((1, "a1"), (1, "a2"), (2, "b1")):
        queue.add(Q(requester, name))
    assert names(queue) == ["a1", "a2", "b1"]


def test_switching_type_keeps_tracks():
    queue = TrackQueue(fair=False)
    queue.add(Q(1, "a1"))
    queue.add(Q(1, "a2"))
    queue.fair = True
    queue.add(Q(2, "b1"))
    assert names(queue) == ["a1", "b1", "a2"]


def test_remove_all_and_shuffle_only_touch_one_requester():
    queue = TrackQueue(fair=False)
    for requester, name in ((1, "a1"), (2, "b1"), (1, "a2"), (2, "b2"), (1, "a3")):
        queue.add(Q(requester, name))

    assert queue.shuffle(1, random.Random(4)) == 3
    assert [names(queue)[i] for i in (1, 3)] == ["b1", "b2"]
    assert sorted(names(queue)[i] for i in (0, 2, 4)) == ["a1", "a2", "a3"]

    assert queue.remove_all(2) == 2
    assert sorted(names(queue)) == ["a1", "a2", "a3"]


def test_add_at_pull_skip_and_move():
    queue = TrackQueue(fair=False)
    for name in "abcd":
        queue.add(Q(1, name))
    queue.add_at(99, Q(1, "e"))
    queue.add_at(0, Q(1, "z"))
    assert queue.pull().name == "z"
    queue.skip(2)
    assert names(queue) == ["c", "d", "e"]
    assert queue.move(2, 0).name == "e"
    assert names(queue) == ["e", "c", "d"]
