import random

import pytest

from pymusicbot.audio.sources import _looks_like_path, attached_playlist, youtube_video_id
from pymusicbot.playlists import PlaylistError, PlaylistStore, ShuffleMode, clean_name


@pytest.fixture
def store(tmp_path):
    return PlaylistStore(tmp_path / "Playlists")


def test_jmusicbot_format(store):
    store.folder.mkdir()
    (store.folder / "Mix.txt").write_text(
        "// my mix\n\nhttps://youtu.be/abc\n  Album/01 First.mp3  \n# a comment\nsome search words\n",
        encoding="utf-8",
    )
    playlist = store.load("Mix")
    assert playlist.items == ["https://youtu.be/abc", "Album/01 First.mp3", "some search words"]
    assert playlist.mode == ShuffleMode.OFF


def test_shuffle_marker(store):
    store.folder.mkdir()
    (store.folder / "Mix.txt").write_text("# shuffle\n" + "\n".join("abcdefgh"), encoding="utf-8")
    playlist = store.load("Mix", rng=random.Random(1))
    assert playlist.mode == ShuffleMode.SHUFFLE
    assert sorted(playlist.items) == list("abcdefgh")
    assert playlist.items != list("abcdefgh")


def test_create_append_delete(store):
    assert store.names() == []
    assert store.create("road trip") == "road_trip"
    with pytest.raises(PlaylistError, match="already exists"):
        store.create("road_trip")
    (store.folder / "road_trip.txt").write_text("#shuffle\nfirst", encoding="utf-8")  # no trailing newline
    store.append("road trip", ["second", "third"])
    assert (store.folder / "road_trip.txt").read_text(encoding="utf-8") == "#shuffle\nfirst\nsecond\nthird\n"
    assert store.names() == ["road_trip"]
    store.delete("road_trip")
    assert store.names() == []
    with pytest.raises(PlaylistError, match="doesn't exist"):
        store.delete("road_trip")


@pytest.mark.parametrize("name", ["", "/", "..", "../"])
def test_unusable_names(store, name):
    with pytest.raises(PlaylistError):
        store.create(name)


def test_names_cannot_leave_the_folder(store):
    assert clean_name("../../etc/passwd") == "....etcpasswd"
    assert store.load("../secret") is None


def test_attached_playlist():
    assert attached_playlist("https://www.youtube.com/watch?v=abc&list=PL1") == "https://www.youtube.com/playlist?list=PL1"
    assert attached_playlist("https://youtu.be/abc?list=PL1") == "https://www.youtube.com/playlist?list=PL1"
    assert attached_playlist("https://www.youtube.com/watch?v=abc") is None
    assert attached_playlist("https://www.youtube.com/playlist?list=PL1") is None  # already a playlist
    assert attached_playlist("https://soundcloud.com/a/b?list=x") is None


def test_youtube_video_id():
    assert youtube_video_id("https://www.youtube.com/watch?v=abc&list=PL1") == "abc"
    assert youtube_video_id("https://youtu.be/abc") == "abc"
    assert youtube_video_id("https://example.com/") is None


@pytest.mark.parametrize(
    ("query", "expected"),
    [("/home/dan/Music/song.mp3", True), ("Album/song.flac", True), ("~/x", True), ("lofi beats", False),
     ("https://example.com/song.mp3", False)],
)
def test_looks_like_path(query, expected):
    assert _looks_like_path(query) is expected


def test_remove_keeps_comments_and_counts_only_entries(store):
    store.folder.mkdir()
    (store.folder / "Mix.txt").write_text("#shuffle\n\n# Album\nfirst\nsecond\n// note\nthird\n", encoding="utf-8")
    assert store.entries("Mix") == ["first", "second", "third"]
    assert store.remove("Mix", 2) == ("Mix", "second")
    assert (store.folder / "Mix.txt").read_text(encoding="utf-8") == "#shuffle\n\n# Album\nfirst\n// note\nthird\n"
    with pytest.raises(PlaylistError, match="between 1 and 2"):
        store.remove("Mix", 3)


def test_expand_entries(tmp_path):
    import asyncio

    from pymusicbot.audio.local import LocalLibrary
    from pymusicbot.commands.playlists import expand_entries

    album = tmp_path / "music" / "Girls Band Cry" / "Diamond Dust"
    album.mkdir(parents=True)
    for name in ("b.opus", "a.opus", "cover.jpg"):
        (album / name).write_bytes(b"")
    library = LocalLibrary((tmp_path / "music",))

    expanded, problems = asyncio.run(expand_entries(library, [
        "Girls Band Cry/Diamond Dust/",
        str(album / "a.opus"),  # absolute paths inside the library are made relative
        "https://youtu.be/x",
        "search:some words",
    ]))
    assert problems == []
    assert expanded == [
        "Girls Band Cry/Diamond Dust/a.opus",
        "Girls Band Cry/Diamond Dust/b.opus",
        "Girls Band Cry/Diamond Dust/a.opus",
        "https://youtu.be/x",
        "search:some words",
    ]

    _, problems = asyncio.run(expand_entries(library, ["DiamondDust"]))
    assert problems == ["`DiamondDust` isn't in the music library. Did you mean `Girls Band Cry/Diamond Dust/`?"]


def test_random_marker_keeps_file_order(store):
    store.folder.mkdir()
    (store.folder / "Mix.txt").write_text("// random\na\nb\nc\n", encoding="utf-8")
    playlist = store.load("Mix")
    assert playlist.mode == ShuffleMode.RANDOM
    assert playlist.items == ["a", "b", "c"]  # picked from at play time


def test_set_mode_replaces_the_marker_and_keeps_comments(store):
    store.folder.mkdir()
    path = store.folder / "Mix.txt"
    path.write_text("# my mix\na\n#shuffle\nb\n", encoding="utf-8")
    store.set_mode("Mix", ShuffleMode.RANDOM)
    assert path.read_text(encoding="utf-8") == "#random\n# my mix\na\nb\n"
    store.set_mode("Mix", ShuffleMode.OFF)
    assert path.read_text(encoding="utf-8") == "# my mix\na\nb\n"
    assert store.load("Mix").mode == ShuffleMode.OFF
