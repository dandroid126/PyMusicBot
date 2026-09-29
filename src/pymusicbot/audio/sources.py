"""Turning what a user typed into tracks: local files and folders, URLs, and searches."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yt_dlp

from ..config import Config
from ..timeutil import parse_unit_time
from .local import LocalLibrary, probe
from .track import Requester, Track

log = logging.getLogger(__name__)

STREAM_URL_MAX_AGE = 30 * 60  # seconds a fetched stream URL is reused before fetching a new one
PROBE_CONCURRENCY = 8

# Prefer a continuous HTTP stream over HLS (m3u8) when a site offers both.
_FORMAT = "bestaudio[protocol^=http]/bestaudio/best"
_YOUTUBE_TIMESTAMP = re.compile(r"youtu(?:\.be|be\..+)/.*\?.*(?!.*list=)t=([\dhms]+)")


class SourceError(Exception):
    """A query couldn't be loaded; the message is shown to the user."""


@dataclass
class Resolved:
    tracks: list[Track]
    playlist_title: str | None = None  # set when the query was a playlist or folder
    too_long: list[Track] = field(default_factory=list)  # left out for exceeding max_track_length


class Sources:
    def __init__(self, config: Config, library: LocalLibrary):
        self.config = config
        self.library = library

    async def resolve(self, query: str, requester: Requester | None) -> Resolved:
        query = query.strip()
        if query.startswith("<") and query.endswith(">"):  # Discord's no-embed link syntax
            query = query[1:-1]

        if (path := self.library.resolve(query)) is not None:
            resolved = await self._local(path, requester)
        elif query.startswith(("http://", "https://")):
            resolved = await self._online(query, requester)
        else:
            resolved = await self._online(f"ytsearch1:{query}", requester)

        if not resolved.tracks:
            raise SourceError("No results found." if resolved.playlist_title is None else "That playlist is empty.")
        limit = self.config.player.max_track_length
        if limit:
            resolved.too_long = [t for t in resolved.tracks if t.duration is not None and t.duration > limit]
            resolved.tracks = [t for t in resolved.tracks if t not in resolved.too_long]
        return resolved

    async def search(self, query: str, site: str = "ytsearch", count: int = 5) -> list[Track]:
        """Search results for picking from, without a requester yet."""
        info = await self._extract(f"{site}{count}:{query}")
        return [t for t in (_track_from_info(e, None) for e in info.get("entries") or []) if t]

    async def ensure_stream(self, track: Track) -> None:
        """Fetch the stream URL for an online track unless a recent one is cached."""
        if track.local or (track.stream_url and time.monotonic() - track.stream_fetched_at < STREAM_URL_MAX_AGE):
            return
        info = await self._extract(track.source, flat=False)
        if info.get("_type") == "playlist":  # a search query; take the first result
            entries = [e for e in info.get("entries") or [] if e]
            if not entries:
                raise SourceError("That track is no longer available.")
            info = entries[0]
        if not info.get("url"):
            raise SourceError("No playable stream was found for that track.")
        track.stream_url = info["url"]
        track.stream_user_agent = (info.get("http_headers") or {}).get("User-Agent")
        track.stream_fetched_at = time.monotonic()

    async def _local(self, path: Path, requester: Requester | None) -> Resolved:
        if path.is_file():
            return Resolved([await self._local_track(path, requester)])
        files = await asyncio.to_thread(self.library.audio_files, path)
        semaphore = asyncio.Semaphore(PROBE_CONCURRENCY)

        async def probe_one(file: Path) -> Track:
            async with semaphore:
                return await self._local_track(file, requester)

        tracks = await asyncio.gather(*(probe_one(f) for f in files))
        return Resolved(list(tracks), playlist_title=self.library.display_path(path) or path.name)

    async def _local_track(self, path: Path, requester: Requester | None) -> Track:
        title, duration = await probe(path)
        return Track(title=title, source=str(path), duration=duration, requester=requester, local=True)

    async def _online(self, query: str, requester: Requester | None) -> Resolved:
        info = await self._extract(query)
        start = _youtube_start(query)
        if info.get("_type") == "playlist":
            tracks = [t for t in (_track_from_info(e, requester) for e in info.get("entries") or []) if t]
            if query.startswith("ytsearch1:"):  # a search is a one-entry "playlist"
                return Resolved(tracks[:1])
            return Resolved(tracks, playlist_title=info.get("title") or "a playlist")
        track = _track_from_info(info, requester)
        if track is None:
            raise SourceError("That link couldn't be loaded.")
        track.start_offset = start
        if info.get("url") and info["url"] != track.source:
            track.stream_url = info["url"]  # extraction already found the stream; reuse it
            track.stream_user_agent = (info.get("http_headers") or {}).get("User-Agent")
            track.stream_fetched_at = time.monotonic()
        return Resolved([track])

    async def _extract(self, query: str, flat: bool = True) -> dict[str, Any]:
        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": _FORMAT,
            "playlistend": self.config.player.max_playlist_tracks or None,
        }
        if flat:
            options["extract_flat"] = "in_playlist"  # list playlist entries without extracting each one

        def run() -> dict[str, Any]:
            with yt_dlp.YoutubeDL(options) as ydl:
                return ydl.sanitize_info(ydl.extract_info(query, download=False))

        try:
            return await asyncio.to_thread(run)
        except yt_dlp.utils.DownloadError as e:
            message = str(e).removeprefix("ERROR: ")
            log.info("Couldn't load %s: %s", query, message)
            raise SourceError(message) from None


def _track_from_info(info: dict[str, Any] | None, requester: Requester | None) -> Track | None:
    if not info:
        return None
    source = info.get("webpage_url") or info.get("url")
    if not source:
        return None
    live = info.get("is_live") or info.get("live_status") == "is_live"
    duration = info.get("duration")
    thumbnails = info.get("thumbnails") or []
    return Track(
        title=info.get("title") or source,
        source=source,
        duration=None if live or duration is None else float(duration),
        requester=requester,
        uploader=info.get("uploader") or info.get("channel"),
        thumbnail=info.get("thumbnail") or (thumbnails[-1].get("url") if thumbnails else None),
    )


def _youtube_start(url: str) -> float:
    match = _YOUTUBE_TIMESTAMP.search(url)
    if not match:
        return 0.0
    milliseconds = parse_unit_time(match.group(1))
    return max(milliseconds, 0) / 1000
