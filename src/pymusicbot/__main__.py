"""Entry point: python -m pymusicbot"""

from __future__ import annotations

import logging
import sys

import discord

from . import __version__, shutdown
from .bot import MusicBot
from .config import ConfigError, load_config
from .settings import SettingsError, SettingsStore

log = logging.getLogger("pymusicbot")


def main() -> int:
    discord.utils.setup_logging(level=logging.INFO)
    shutdown.install_early_handler()
    log.info("PyMusicBot %s starting", __version__)

    try:
        config = load_config()
    except ConfigError as e:
        log.error("Config error: %s", e)
        return 2
    logging.getLogger().setLevel(config.log_level.upper())

    try:
        settings = SettingsStore.load(config.data_dir)
    except SettingsError as e:
        log.error("Settings error: %s", e)
        return 2

    bot = MusicBot(config, settings)
    try:
        bot.run(config.token, log_handler=None)
    except discord.LoginFailure:
        log.error("Discord rejected the token. Check DISCORD_TOKEN in .env (it's the bot token, not the client secret).")
        return 2
    log.info("Disconnected from Discord; exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
