# Running PyMusicBot

This guide takes you from nothing to a bot playing music in your Discord server. You don't need
to know Python or Docker; copy the commands as they are.

There are four ways to run the bot. Pick one:

| Way | Good for | You install |
| --- | --- | --- |
| [Docker Compose](#docker-compose-linux-server) | A Linux server that runs all the time (recommended) | Docker |
| [Docker Desktop on Windows](#docker-desktop-on-windows) | A Windows PC, kept separate from the rest of the system | Docker Desktop |
| [Python on Windows](#python-on-windows) | A Windows PC, the simplest setup | Python, FFmpeg |
| [Python on Linux](#python-on-linux) | A Linux machine without Docker | Python, FFmpeg |

Everything else the bot needs comes with it. With Docker you download a ready-made image and
two small files, not the bot's code. Docker users who prefer plain `docker run` over Compose:
see [docker run](#docker-run).

Contents: [Create the bot in Discord](#1-create-the-bot-in-discord) ·
[The files you edit](#2-the-files-you-edit) · [Start the bot](#3-start-the-bot) ·
[Invite it and test](#4-invite-it-and-test) ·
[Who can use which commands](#who-can-use-which-commands) · [Updating](#updating) · [Backups](#backups) ·
[Moving from JMusicBot](#moving-from-jmusicbot) · [Troubleshooting](#troubleshooting)

## 1. Create the bot in Discord

Every copy of PyMusicBot logs in as its own Discord bot, which you create once:

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and sign in.
2. Click **New Application**, give it a name (this is the bot's name), accept the terms and click
   **Create**.
3. Open **Installation** on the left. Set **Install Link** to **None** and click **Save Changes**.
4. Open **Bot** on the left. Turn off **Public Bot**, so only you can add the bot to servers,
   and save.
5. On the same page, click **Reset Token**, confirm, and copy the token. Keep it somewhere
   safe for the next step. Anyone with the token controls your bot; if it leaks, reset it here.

The bot needs no privileged intents, so leave those switches off. You're the bot's owner (the
one who can use owner commands) because you own the application. To have more than one owner,
list everyone's Discord user ID in `owner_id` in `config/config.toml`, e.g.
`owner_id = [123, 456]` (see `config.example.toml`).

Coming from JMusicBot? You can reuse its application and token, and skip this step.

## 2. The files you edit

| File | What goes in it |
| --- | --- |
| `.env` | Your bot token, and where your music is. Made from `.env.example`. |
| `config/config.toml` | Optional. Changes to how the bot behaves. Made from `config.example.toml`. |

`.env` looks like this:

```ini
DISCORD_TOKEN=paste-your-token-here
MUSIC_DIR=/srv/music
```

- `DISCORD_TOKEN` is the token from step 1.
- `MUSIC_DIR` is the folder with your music, for `/play` with local files. Leave it out if you
  only play online music. On Windows, write it with forward slashes: `MUSIC_DIR=D:/Music`.
- Write values without quotes.

You don't need `config/config.toml` to get started. Every setting has a default. To change
one, save
[`config.example.toml`](https://github.com/dandroid126/PyMusicBot/blob/main/config.example.toml)
as `config/config.toml` and edit it; its comments explain each setting. Restart the bot
afterwards.

The bot writes its own files to `data/`: `settings.json` (what `/setdj`, `/volume` and other
commands change) and `Playlists/`. Keep that folder when you update.

## 3. Start the bot

Follow the section for the way you picked.

### Docker Compose (Linux server)

You don't need the bot's code: Docker downloads the ready-made image.

1. Install Docker Engine by following
   [Docker's instructions for your distribution](https://docs.docker.com/engine/install/).
   Compose comes with it.
2. Make a folder for the bot, with `config` and `data` folders in it, and download the two
   files it needs:

   ```sh
   mkdir -p pymusicbot/config pymusicbot/data
   cd pymusicbot
   curl -fsSLO https://raw.githubusercontent.com/dandroid126/PyMusicBot/main/compose.yaml
   curl -fsSL https://raw.githubusercontent.com/dandroid126/PyMusicBot/main/.env.example -o .env
   ```

3. Fill in `.env` (step 2). `nano` saves with Ctrl+O and quits with Ctrl+X.

   ```sh
   nano .env
   ```

4. Download the image and start the bot in the background:

   ```sh
   docker compose up -d
   ```

5. Watch the log (Ctrl+C stops watching, not the bot):

   ```sh
   docker compose logs -f
   ```

Stop the bot with `docker compose stop` and start it again with `docker compose start`.

The bot runs as user ID 1000 inside the container, never as root. It must be able to write to
`data/` and read your music. If the log says `Permission denied` for `/data`, which happens when
your own user ID isn't 1000 (check with `id -u`) or with rootless Docker or Podman, hand the
folder to the bot by running this in the bot's folder (with Podman, write `podman` instead of
`docker`):

```sh
docker run --rm --user 0 -v "$PWD/data:/data" --entrypoint chown ghcr.io/dandroid126/pymusicbot -R 1000:1000 /data
```

Your music must be readable by everyone, which is the usual setting. On a network share (NFS,
SMB), the share must allow that too.

**The bot doesn't restart by itself**: not after `/shutdown`, a crash or a reboot. Start it
again with `docker compose up -d`. To change that, pick one:

- **Restart after crashes:** create a file named `compose.override.yaml` next to
  `compose.yaml` with the lines below, then run `docker compose up -d`. A setup error, such as
  a wrong token, then makes Docker restart the bot over and over until you fix it.

  ```yaml
  services:
    pymusicbot:
      restart: on-failure
  ```

  With `restart: unless-stopped` instead, the bot also comes back after reboots, and
  `/shutdown` restarts it instead of stopping it; stop it with `docker compose stop`.
- **A systemd service** starts the bot with the server and restarts it after crashes, but not
  after `/shutdown` or a setup error. Create `/etc/systemd/system/pymusicbot.service`
  (`sudo nano /etc/systemd/system/pymusicbot.service`), with the bot's folder as
  `WorkingDirectory`:

  ```ini
  [Unit]
  Description=PyMusicBot
  Requires=docker.service
  After=docker.service network-online.target
  Wants=network-online.target

  [Service]
  WorkingDirectory=/home/yourname/pymusicbot
  ExecStart=/usr/bin/docker compose up --exit-code-from pymusicbot
  ExecStop=/usr/bin/docker compose stop
  # Restart after a crash, but not after /shutdown or a setup error (exit code 2).
  Restart=on-failure
  RestartPreventExitStatus=2
  RestartSec=10

  [Install]
  WantedBy=multi-user.target
  ```

  Then turn it on with `sudo systemctl daemon-reload` and
  `sudo systemctl enable --now pymusicbot`, and from then on start and stop the bot with
  `sudo systemctl start pymusicbot` and `sudo systemctl stop pymusicbot`. The log is in
  `journalctl -u pymusicbot -f`.

**More than one bot on one machine:** give each bot its own folder (with its own `.env`,
`config` and `data`) and its own name, by adding a line like `COMPOSE_PROJECT_NAME=my_bot` to
each `.env`. Without it, every copy is named `pymusicbot`, and starting one replaces the other.

Put any other changes to `compose.yaml` in `compose.override.yaml` too, so downloading a new
`compose.yaml` when you update doesn't undo them.

### docker run

For Docker without Compose. Make the folders and `.env` as in steps 2 and 3 of Docker Compose
(you don't need `compose.yaml`), then, from that folder:

```sh
docker run -d --name pymusicbot \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true \
  --env-file .env \
  -v "$PWD/config:/config:ro" \
  -v "$PWD/data:/data" \
  -v /srv/music:/music:ro \
  ghcr.io/dandroid126/pymusicbot:latest
```

Replace `/srv/music` with your music folder. `MUSIC_DIR` in `.env` isn't used here; the `-v`
line mounts the music instead. The second line locks the container down the same way
`compose.yaml` does. See the log with `docker logs -f pymusicbot`. The notes on permissions
and restarts under Docker Compose apply here too: add `--restart on-failure` or
`--restart unless-stopped` to the first line for the same behavior.

### Docker Desktop on Windows

1. Install [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)
   and start it once.
2. Make a folder for the bot, such as `C:\PyMusicBot`. Open it in File Explorer, click the
   address bar, type `cmd` and press Enter. A command window opens in the folder.
3. Make the `config` and `data` folders and download the two files the bot needs:

   ```bat
   mkdir config data
   curl -fsSLO https://raw.githubusercontent.com/dandroid126/PyMusicBot/main/compose.yaml
   curl -fsSL https://raw.githubusercontent.com/dandroid126/PyMusicBot/main/.env.example -o .env
   ```

4. Fill in `.env` (step 2), then save and close Notepad:

   ```bat
   notepad .env
   ```

5. Download the image and start the bot:

   ```bat
   docker compose up -d
   ```

The bot now shows under **Containers** in Docker Desktop, with its log and Start and Stop
buttons. The bot doesn't restart by itself, including after a restart of Windows: start it
again with the Start button. The note on restarts under
[Docker Compose](#docker-compose-linux-server) explains how to change that.

### Python on Windows

1. Install Python and FFmpeg. The easiest way: open a command window (press Windows+R, type
   `cmd`, press Enter) and run:

   ```bat
   winget install Python.Python.3.14
   winget install Gyan.FFmpeg
   ```

   Or install Python 3.12 or newer from [python.org](https://www.python.org/downloads/).
   Close the window afterwards; new programs are found only in new windows.
2. On the [PyMusicBot GitHub page](https://github.com/dandroid126/PyMusicBot), click
   **Code**, then **Download ZIP**. Extract it; you get a folder named `PyMusicBot-main`,
   which you can move anywhere.
3. Double-click `start.bat` in that folder. If Windows says it protected your PC, click
   **More info**, then **Run anyway**.
4. The first time, Notepad opens `.env`. Paste your token after `DISCORD_TOKEN=`, add your
   `MUSIC_DIR` (step 2), save and close Notepad.

`start.bat` then installs what the bot needs (a few minutes, only the first time) and starts it.
The window shows the log. Closing the window stops the bot; double-click `start.bat` to start
it again.

### Python on Linux

You need Python 3.12 or newer and FFmpeg. On Ubuntu 24.04 or newer and Debian 13 or newer:

```sh
sudo apt install git python3-venv ffmpeg
```

(Debian 12 has Python 3.11, which is too old; use Docker there.) Then:

```sh
git clone https://github.com/dandroid126/PyMusicBot.git
cd PyMusicBot
cp .env.example .env
nano .env
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.txt
.venv/bin/pip install --no-deps -e .
.venv/bin/python -m pymusicbot
```

This runs the bot in the terminal; Ctrl+C stops it. To run it in the background and start it
with the computer, make it a systemd service. Create `/etc/systemd/system/pymusicbot.service`
(`sudo nano /etc/systemd/system/pymusicbot.service`), replacing `yourname` with your user name
and the paths with where the bot is:

```ini
[Unit]
Description=PyMusicBot
Wants=network-online.target
After=network-online.target

[Service]
User=yourname
WorkingDirectory=/home/yourname/PyMusicBot
ExecStart=/home/yourname/PyMusicBot/.venv/bin/python -m pymusicbot
# Restart after a crash, but not after /shutdown or a setup error (exit code 2).
Restart=on-failure
RestartPreventExitStatus=2
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then turn it on, and see its log:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now pymusicbot
journalctl -u pymusicbot -f
```

## 4. Invite it and test

When the bot isn't in any server yet, its log shows a line like:

```text
The bot isn't in any servers yet. Invite it with: https://discord.com/oauth2/authorize?client_id=...
```

Open that link, pick your server and click **Authorize**. The link already asks for the
permissions the bot needs. Then, in your server:

1. Join a voice channel.
2. Type `/play` and a song name, e.g. `/play never gonna give you up`.

If you set `MUSIC_DIR`, try `/play` with a file name too: suggestions from your music folder
appear as you type.

If slash commands don't show up yet, press Ctrl+R in Discord to reload it.

## Who can use which commands

Out of the box, the bot has four levels, and it checks them every time a command runs:

| Who | Can use |
| --- | --- |
| Everyone | Playing and queueing music, `/queue`, `/skip` (as a vote), `/settings`, playing playlists |
| DJs: members with the role set by `/setdj` | Also `/pause`, `/stop`, `/volume`, `/forceskip` and the other queue controls |
| Admins: members with Manage Server | Also the server settings: `/setdj`, `/setvc`, `/setskip`, `/queuetype` |
| Owners: `owner_id` in `config.toml`, or the application's owner | Everything, including editing playlists and `/shutdown` |

Admin commands are hidden from members without Manage Server. To decide which channels the
commands work in, or to hide them from more people, use Discord's own command permissions. They
can be set per role, per member and per channel, for all commands or one at a time. (JMusicBot's
`settc` text channel setting isn't used; `/settc` explains these steps instead.)

1. Open Server Settings → **Integrations**, and under Bots and Apps click the bot's **Manage**
   button. You need the Manage Server permission.
2. **Only in some channels:** under **Channels**, set **All Channels** to ✕ and add the channels
   where the commands should work, with ✓.
3. **Only for some roles:** under **Roles & Members**, click **Add Roles or Members**, add the
   roles that should see the commands with ✓, then set **@everyone** to ✕.
4. Click **Save Changes**.

Members who can't use a command don't see it at all. Some things to know:

- **Members with the Administrator permission always see every command.** Discord doesn't allow
  hiding commands from them. The bot's own levels above still apply to them.
- **What you set in steps 2 and 3 applies to every command that has no settings of its own.**
  That includes the admin and owner commands, so the roles you allow in step 3 see those too.
  Running them is still refused unless the member is an admin or an owner. To hide one, click it
  in the **Commands** list, add the role and set it to ✕. A command with its own settings ignores
  the ones from steps 2 and 3.
- **Members with Manage Server but not Administrator** lose sight of the admin commands once
  @everyone is ✕. Add their role in step 3 if they need them.
- **`/playlist` is one command** with subcommands, and Discord sets permissions per command, so
  `/playlist play` and `/playlist create` are shown or hidden together.

To check the result, try it from an account without the Administrator permission: with and
without one of the allowed roles, and in a channel that isn't allowed.

## Updating

Update when YouTube stops playing, as well as now and then: new versions bring a newer
yt-dlp, the part that talks to YouTube and other sites. Your `.env`, `config/` and `data/` are
kept.

| Way | How to update |
| --- | --- |
| Docker Compose, Docker Desktop | In the bot's folder: download `compose.yaml` again (the `curl -fsSLO` line from setup), then `docker compose pull` and `docker compose up -d` (with the systemd service: `sudo systemctl restart pymusicbot` instead of `up -d`) |
| docker run | `docker pull ghcr.io/dandroid126/pymusicbot:latest`, `docker rm -f pymusicbot`, then the `docker run` command again |
| Python on Windows | Stop the bot. Download and extract the new ZIP, copy `.env`, `config` and `data` from the old folder into the new one, and double-click `start.bat` in the new folder; it installs what changed. Use the new folder from now on. |
| Python on Linux | `git pull`, `.venv/bin/pip install --require-hashes -r requirements.txt`, then restart the bot (`sudo systemctl restart pymusicbot`) |

## Backups

Back up `.env`, the `config` folder and the `data` folder. They're all the bot needs to come
back exactly as it was on a new machine. (Your music folder is yours to back up separately.)

## Moving from JMusicBot

1. Stop JMusicBot.
2. Put your JMusicBot token in `.env` as `DISCORD_TOKEN`. The bot stays in your servers.
3. Copy JMusicBot's `serversettings.json` into `data/`. On the first start it's converted into
   `data/settings.json`. The old file is left as it was and isn't read again.
4. Copy your `Playlists` folder into `data/`. The file format is the same.
5. Optional: move your `config.txt` settings into `config/config.toml`. The comments in
   `config.example.toml` give each old setting's new name. Commands are slash commands now, so
   the prefix settings are gone.
6. If you used `settc` to limit the bot to one text channel: that setting isn't carried over. Set
   it up with Discord's command permissions instead (see
   [Who can use which commands](#who-can-use-which-commands)).

## Troubleshooting

The log says what went wrong. Where to find it: `docker compose logs` (Docker Compose), the
container's **Logs** tab (Docker Desktop), the `start.bat` window (Python on Windows) or
`journalctl -u pymusicbot` (a systemd service).

| The log or Discord says | What to do |
| --- | --- |
| `DISCORD_TOKEN is not set` | Put the token in `.env` (step 2). The file must be named exactly `.env` and be in the bot's folder. |
| `Discord rejected the token` | Reset the token in the Developer Portal (step 1) and paste the new one. It's the token from the **Bot** page, not the client secret. |
| `Config error: ...` | A setting in `config/config.toml` is wrong; the message names it. |
| `ffmpeg wasn't found` | Install FFmpeg (see your way in step 3), then open a new window or restart the service. Docker already has it. |
| `Deno wasn't found` | Reinstall the requirements. On Windows, delete the `.venv` folder and run `start.bat` again. |
| `Music folder ... doesn't exist` | Check `MUSIC_DIR` in `.env`. With `docker run`, check the `-v ...:/music:ro` line. |
| `Permission denied` for `/data` (Docker on Linux) | Run the `chown` command under [Docker Compose](#docker-compose-linux-server). |
| `Python 3.12 or newer wasn't found` (`start.bat`) | Install Python (step 3), close the window, and double-click `start.bat` again. |
| `'Public Bot' is on` | Turn it off (step 1, points 3 and 4). Until then, anyone with the bot's ID can add it to their server. |
| `Links can only be played from ...` | Links from other sites are refused on purpose, so nobody can make the bot load other pages, such as ones on your own network. To allow another site, add it to `allowed_sites` in `config/config.toml` (see `config.example.toml`); it works only if yt-dlp supports that site. |
| Online songs fail to load | Update PyMusicBot (see [Updating](#updating)). Sites like YouTube change often, and each update brings the fixes. |
| Slash commands are missing | Press Ctrl+R in Discord. Commands also need the bot invited with the `applications.commands` scope: in the Developer Portal, open **OAuth2**, tick `bot` and `applications.commands` under **OAuth2 URL Generator**, and open the link it makes. |
