import asyncio
import os
import subprocess

import pytest

from pymusicbot.audio.local import LocalLibrary, probe


@pytest.fixture
def library(tmp_path):
    music = tmp_path / "music"
    (music / "Album").mkdir(parents=True)
    (music / "Album" / "01 First.mp3").write_bytes(b"")
    (music / "Album" / "02 Second.flac").write_bytes(b"")
    (music / "Album" / "cover.jpg").write_bytes(b"")
    (music / "single.ogg").write_bytes(b"")
    outside = tmp_path / "secret.mp3"
    outside.write_bytes(b"")
    os.symlink(outside, music / "escape.mp3")
    os.symlink(tmp_path, music / "escape-dir")
    return LocalLibrary((music,))


def test_resolve_files_and_folders(library):
    music = library.folders[0]
    assert library.resolve("single.ogg") == music / "single.ogg"
    assert library.resolve("Album") == music / "Album"
    assert library.resolve(str(music / "single.ogg")) == music / "single.ogg"  # absolute, but inside


@pytest.mark.parametrize("query", ["", "../secret.mp3", "/etc/passwd", "escape.mp3", "escape-dir", "Album/cover.jpg", "missing.mp3"])
def test_resolve_refuses(library, query):
    assert library.resolve(query) is None


def test_audio_files_sorted_and_contained(library):
    files = library.audio_files(library.folders[0])
    assert [f.name for f in files] == ["01 First.mp3", "02 Second.flac", "single.ogg"]


def test_search_matches_every_word_and_lists_folders_first(library):
    results = asyncio.run(library.search(""))
    assert results[0] == "Album/"
    assert asyncio.run(library.search("album second")) == ["Album/02 Second.flac"]


def test_probe_reads_tags(tmp_path):
    path = tmp_path / "tone.mp3"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=2", "-metadata", "title=Tone",
         "-metadata", "artist=Tester", str(path)],
        check=True,
    )
    title, duration = asyncio.run(probe(path))
    assert title == "Tester - Tone"
    assert duration == pytest.approx(2, abs=0.1)


def test_probe_falls_back_to_file_name(tmp_path):
    path = tmp_path / "Untagged Song.mp3"
    path.write_bytes(b"not audio")
    assert asyncio.run(probe(path)) == ("Untagged Song", None)


@pytest.mark.parametrize(
    ("typed", "suggested"),
    [("DiamondDust", "Girls Band Cry/Diamond Dust/"), ("diamond dust", "Girls Band Cry/Diamond Dust/"),
     ("cycle of sorrow", "Girls Band Cry/Diamond Dust/Cycle Of Sorrow.opus"), ("xyzzy", None)],
)
def test_suggest_close_library_paths(tmp_path, typed, suggested):
    folder = tmp_path / "music" / "Girls Band Cry" / "Diamond Dust"
    folder.mkdir(parents=True)
    (folder / "Cycle Of Sorrow.opus").write_bytes(b"")
    library = LocalLibrary((tmp_path / "music",))
    assert asyncio.run(library.suggest(typed)) == suggested
