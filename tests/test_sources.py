"""How queries become tracks, with yt-dlp replaced by a fake that records its calls."""

import asyncio

import pytest

from pymusicbot.audio import sources as sources_module
from pymusicbot.audio.local import LocalLibrary
from pymusicbot.audio.sources import Sources, SourceError
from pymusicbot.audio.track import Requester, Track
from pymusicbot.config import load_config

ME = Requester(7, "me")


def video(video_id, title=None, duration=200, **extra):
    return {"title": title or video_id, "webpage_url": f"https://www.youtube.com/watch?v={video_id}", "duration": duration, **extra}


class FakeYtDlp:
    def __init__(self, results):
        self.results = results  # query -> info dict, or an exception to raise
        self.calls = []

    async def __call__(self, query, flat=True):
        self.calls.append((query, flat))
        result = self.results.get(query)
        if isinstance(result, Exception):
            raise result
        if result is None:
            raise SourceError(f"no fake result for {query}")
        return result


@pytest.fixture
def make_sources(tmp_path):
    (tmp_path / "music").mkdir()

    def factory(results, config_toml=""):
        (tmp_path / "config.toml").write_text(config_toml, encoding="utf-8")
        config = load_config({"DISCORD_TOKEN": "x", "PYMUSICBOT_CONFIG": str(tmp_path / "config.toml"),
                              "PYMUSICBOT_DATA": str(tmp_path / "data")})
        sources = Sources(config, LocalLibrary((tmp_path / "music",)))
        sources._extract = FakeYtDlp(results)
        return sources

    return factory


def run(coro):
    return asyncio.run(coro)


def test_words_search_youtube_for_one_result(make_sources):
    sources = make_sources({"ytsearch1:lofi beats": {"_type": "playlist", "entries": [video("a", "Lofi"), video("b")]}})
    resolved = run(sources.resolve("lofi beats", ME))
    assert [t.title for t in resolved.tracks] == ["Lofi"]
    assert resolved.playlist_title is None
    assert resolved.tracks[0].requester == ME


def test_search_prefix_forces_a_youtube_search(make_sources, tmp_path):
    (tmp_path / "music" / "song.mp3").write_bytes(b"")
    sources = make_sources({"ytsearch1:song.mp3": {"_type": "playlist", "entries": [video("a", "From YouTube")]}})
    resolved = run(sources.resolve("search: song.mp3", ME))
    assert resolved.tracks[0].title == "From YouTube"


def test_library_paths_win_over_searches(make_sources, tmp_path):
    (tmp_path / "music" / "song.mp3").write_bytes(b"")
    sources = make_sources({})
    resolved = run(sources.resolve("song.mp3", ME))
    assert resolved.tracks[0].local and resolved.tracks[0].title == "song"
    assert sources._extract.calls == []


@pytest.mark.parametrize("query", ["/old/machine/song.mp3", "Album/missing.flac", "~/x.mp3"])
def test_paths_missing_from_the_library_are_never_searched(make_sources, query):
    sources = make_sources({})
    with pytest.raises(SourceError, match="isn't in the music library"):
        run(sources.resolve(query, ME))
    assert sources._extract.calls == []


def test_single_video_url_keeps_its_stream_and_start_time(make_sources):
    url = "https://www.youtube.com/watch?v=abc&t=1m5s"
    sources = make_sources({url: video("abc", "Clip", url="https://stream.example/abc", http_headers={"User-Agent": "UA"})})
    track = run(sources.resolve(f"<{url}>", ME)).tracks[0]  # Discord's no-embed brackets are stripped
    assert track.start_offset == 65
    assert track.stream_url == "https://stream.example/abc" and track.stream_user_agent == "UA"


def test_playlist_url_lists_its_entries(make_sources):
    url = "https://www.youtube.com/playlist?list=PL1"
    sources = make_sources({url: {"_type": "playlist", "title": "Mix", "entries": [video("a"), None, video("b")]}})
    resolved = run(sources.resolve(url, ME))
    assert resolved.playlist_title == "Mix"
    assert [t.title for t in resolved.tracks] == ["a", "b"]  # unavailable entries (None) are skipped


def test_live_streams_have_no_duration(make_sources):
    url = "https://www.twitch.tv/somebody"
    sources = make_sources({url: {"title": "Live", "webpage_url": url, "is_live": True, "duration": 5}})
    assert run(sources.resolve(url, ME)).tracks[0].duration is None


def test_tracks_over_the_length_limit_are_set_aside(make_sources):
    url = "https://www.youtube.com/playlist?list=PL1"
    sources = make_sources(
        {url: {"_type": "playlist", "title": "Mix", "entries": [video("short", duration=60), video("long", duration=900)]}},
        "[player]\nmax_track_length = 300\n",
    )
    resolved = run(sources.resolve(url, ME))
    assert [t.title for t in resolved.tracks] == ["short"]
    assert [t.title for t in resolved.too_long] == ["long"]


def test_nothing_found(make_sources):
    sources = make_sources({"ytsearch1:zzz": {"_type": "playlist", "entries": []}})
    with pytest.raises(SourceError, match="No results found"):
        run(sources.resolve("zzz", ME))


def test_extraction_errors_become_source_errors(make_sources):
    url = "https://www.youtube.com/watch?v=gone"
    sources = make_sources({url: SourceError("Video unavailable")})
    with pytest.raises(SourceError, match="Video unavailable"):
        run(sources.resolve(url, ME))


def test_stream_urls_are_cached_then_refetched(make_sources, monkeypatch):
    page = "https://www.youtube.com/watch?v=abc"
    sources = make_sources({page: video("abc", url="https://stream.example/1")})
    track = Track(title="abc", source=page, duration=100)
    clock = [1000.0]
    monkeypatch.setattr(sources_module.time, "monotonic", lambda: clock[0])

    run(sources.ensure_stream(track))
    run(sources.ensure_stream(track))
    assert len(sources._extract.calls) == 1 and track.stream_url == "https://stream.example/1"
    assert sources._extract.calls[0] == (page, False)  # full extraction for the stream

    clock[0] += sources_module.STREAM_URL_MAX_AGE + 1
    run(sources.ensure_stream(track))
    assert len(sources._extract.calls) == 2


def test_stream_for_a_search_entry_uses_the_first_result(make_sources):
    sources = make_sources({"ytsearch1:x": {"_type": "playlist", "entries": [video("a", url="https://stream.example/a")]}})
    track = Track(title="x", source="ytsearch1:x", duration=None)
    run(sources.ensure_stream(track))
    assert track.stream_url == "https://stream.example/a"


def test_stream_missing_is_an_error(make_sources):
    page = "https://www.youtube.com/watch?v=abc"
    sources = make_sources({page: video("abc")})  # no "url"
    with pytest.raises(SourceError, match="No playable stream"):
        run(sources.ensure_stream(Track(title="abc", source=page, duration=100)))


def test_resolve_each_keeps_order_and_reports_errors(make_sources, tmp_path):
    (tmp_path / "music" / "a.mp3").write_bytes(b"")
    sources = make_sources({"ytsearch1:b": {"_type": "playlist", "entries": [video("b")]}})

    async def collect():
        return [(i, item, r) async for i, item, r in sources.resolve_each(["a.mp3", "b", "/gone.mp3"], None)]

    results = run(collect())
    assert [(i, item) for i, item, _ in results] == [(0, "a.mp3"), (1, "b"), (2, "/gone.mp3")]
    assert results[0][2].tracks[0].title == "a" and results[1][2].tracks[0].title == "b"
    assert isinstance(results[2][2], SourceError)


def test_search_returns_several_results_without_requester(make_sources):
    sources = make_sources({"scsearch5:lofi": {"_type": "playlist", "entries": [video(str(i)) for i in range(5)]}})
    results = run(sources.search("lofi", "scsearch"))
    assert len(results) == 5 and all(t.requester is None for t in results)
