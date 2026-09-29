# PyMusicBot

A self-hosted Discord music bot that plays online sources and your own local music files.
It's a Python rewrite of [JMusicBot](https://github.com/jagrosh/MusicBot) by John Grosh,
which stopped working when Discord made voice encryption (DAVE) mandatory in March 2026.

Status: Phase 2 (core player). Playback from URLs, searches and the local music library
works. Playlists, autoplay and the remaining DJ and owner commands come in Phase 3.

## Commands

| Who | Commands |
| --- | --- |
| Everyone | `/play`, `/search`, `/scsearch`, `/local`, `/queue`, `/nowplaying`, `/skip`, `/remove`, `/shuffle`, `/seek`, `/settings` |
| DJs | `/pause`, `/stop`, `/volume`, `/repeat` |
| Admins (Manage Server) | `/setdj`, `/settc`, `/setvc`, `/setskip`, `/queuetype` |
| Owner | `/shutdown` |

`/play` takes a URL, words to search YouTube for, or a file or folder from the music library
(suggestions appear as you type). A folder queues every audio file in it.

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

## Tests

`scripts/test.sh` runs the test suite in Docker.

## Credits

Based on JMusicBot by John Grosh and contributors (Apache License 2.0). Command behavior and
file formats follow the original.
