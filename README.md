# PyMusicBot

A self-hosted Discord music bot that plays online sources and your own local music files.
It's a Python rewrite of [JMusicBot](https://github.com/jagrosh/MusicBot) by John Grosh,
which stopped working when Discord made voice encryption (DAVE) mandatory in March 2026.

Status: Phase 4 (testing and deployment). Everything from JMusicBot that was kept is in place.

**To run the bot, follow [the deployment guide](docs/deployment.md).** It covers Docker
Compose, `docker run`, Docker Desktop on Windows, and Python on Windows (`start.bat`) or Linux,
plus updating, backups and moving from JMusicBot.

## Commands

| Who | Commands |
| --- | --- |
| Everyone | `/play`, `/search`, `/scsearch`, `/local`, `/queue`, `/nowplaying`, `/skip`, `/remove`, `/shuffle`, `/seek`, `/settings`, `/playlist play`, `/playlist list`, `/playlist show` |
| DJs | `/pause`, `/stop`, `/volume`, `/repeat`, `/forceskip`, `/forceremove`, `/movetrack`, `/playnext`, `/skipto` |
| Admins (Manage Server) | `/setdj`, `/settc`, `/setvc`, `/setskip`, `/queuetype` |
| Owner | `/playlist create`, `/playlist append`, `/playlist remove`, `/playlist shuffle`, `/playlist delete`, `/autoplaylist`, `/setgame`, `/setstatus`, `/setname`, `/setavatar`, `/debug`, `/shutdown` |

`/play` takes a URL, words to search YouTube for, or a file or folder from the music library
(suggestions appear as you type). A folder queues every audio file in it.

## Playlists

Playlists are `.txt` files in `data/Playlists/`, in JMusicBot's format: one entry per line (a
URL, a music library path relative to the music folder, or search words), `#` or `//` for
comments. `/playlist shuffle` sets how a playlist plays, stored as a marker line:
none for file order, `#shuffle` for every song once in random order before any repeats, or
`#random` for a random pick from all songs each time. `/playlist append` suggests library paths
as you type and refuses paths that don't exist; start an entry with `search:` to add a YouTube
search on purpose. A server's default playlist
(`/autoplaylist`) plays when the queue runs out; requests always play first.

## Files

| File | Written by | Holds |
| --- | --- | --- |
| `.env` | you | `DISCORD_TOKEN`, and optionally `MUSIC_DIR` (your music folder) and `OWNER_ID` |
| `config/config.toml` | you | Bot behavior; the bot only reads it |
| `data/settings.json` | the bot | Per-server settings changed with slash commands |
| `data/Playlists/` | you or the bot | Playlist files, same format as JMusicBot |

## Dependencies

`requirements.txt` pins the exact, tested version of every package, with hashes, for Windows and
Linux on Python 3.12 or newer. It's the one source of truth: server owners install it with
`pip install -r requirements.txt`, and the Docker image and CI install the same file.

- `pyproject.toml` lists the allowed version ranges that `requirements.txt` is resolved from.
- `scripts/lock.sh` regenerates the file after editing `pyproject.toml` (needs
  [uv](https://docs.astral.sh/uv/); only maintainers need it).
- `scripts/lock.sh --upgrade-package yt-dlp` updates yt-dlp when YouTube breaks it.
- Dependabot opens PRs for new versions of yt-dlp (checked daily), discord.py and pytest. The
  other packages, including Deno (the JavaScript runtime yt-dlp needs, installed from PyPI),
  move together with `scripts/lock.sh --upgrade`. CI installs and tests every PR on Linux and
  Windows, including the guide's install steps and `start.bat`.

## Tests

`scripts/test.sh` runs the test suite in Docker.

## Credits

Based on JMusicBot by John Grosh and contributors (Apache License 2.0). Command behavior and
file formats follow the original.
