# PyMusicBot

A self-hosted Discord music bot that plays online sources and your own local music files.
It's a Python rewrite of [JMusicBot](https://github.com/jagrosh/MusicBot) by John Grosh,
which stopped working when Discord made voice encryption (DAVE) mandatory in March 2026.

Status: Phase 3 (full command set). Everything from JMusicBot that was kept is in place;
Phase 4 is hardening and deployment.

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
| `.env` | you | Secrets: `DISCORD_TOKEN`, optionally `OWNER_ID` and `MUSIC_DIR` |
| `config/config.toml` | you | Bot behavior; the bot only reads it |
| `data/settings.json` | the bot | Per-server settings changed with slash commands |
| `data/Playlists/` | you or the bot | Playlist files, same format as JMusicBot |

## Setup

1. Create a Discord application at https://discord.com/developers/applications and turn
   off **Public Bot**.
2. `cp .env.example .env` and set `DISCORD_TOKEN`. Set `MUSIC_DIR` to your music folder.
3. `cp config.example.toml config/config.toml` and adjust it. Every setting is optional.
4. `docker compose up --build`
5. If the bot isn't in a server yet, the log prints an invite link with the right scopes
   (`bot` and `applications.commands`) and permissions.

## Moving from JMusicBot

- Copy `serversettings.json` into `data/`. On first start it's converted into
  `data/settings.json`. The old file is left as it was and isn't read again.
- Copy your `Playlists` folder into `data/`.
- Move your `config.txt` values into `config/config.toml`. The comments in
  `config.example.toml` explain the new names; the token goes in `.env`.

## Dependencies

`pyproject.toml` lists the allowed version ranges. `uv.lock` pins the exact version and hash of
every package; Docker and CI install from it. `requirements.txt` (and `requirements-test.txt`)
are exported from the lock for installing with plain pip.

- `scripts/lock.sh` re-syncs the requirements files after editing `pyproject.toml`.
- `scripts/lock.sh --upgrade-package yt-dlp` updates yt-dlp when YouTube breaks it.
- Dependabot opens a PR for each yt-dlp release (daily) and one grouped PR for everything else.
  CI tests the new versions, and the requirements files are regenerated on the PR automatically.

## Tests

`scripts/test.sh` runs the test suite in Docker.

## Credits

Based on JMusicBot by John Grosh and contributors (Apache License 2.0). Command behavior and
file formats follow the original.
