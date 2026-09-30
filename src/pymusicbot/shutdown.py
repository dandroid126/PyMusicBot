"""Stopping cleanly on SIGTERM (docker stop, Ctrl+C in docker compose) and SIGINT (Ctrl+C).

The bot is shut down by calling its close() while everything is still running. Raising
KeyboardInterrupt instead makes asyncio cancel every task at once, including the one that reads
from Discord. discord.py's voice disconnect then waits for a confirmation nobody can receive
(up to 30 seconds, ignoring cancellation), and Docker kills the container with exit code 137.

A watchdog makes sure the process still ends in time if something hangs: it logs what
shutdown is waiting on, then exits before Docker's 10-second kill.
"""

from __future__ import annotations

import asyncio
import faulthandler
import logging
import os
import signal
import threading
from collections.abc import Callable, Coroutine
from typing import Any

log = logging.getLogger(__name__)

DIAGNOSE_AFTER = 5  # seconds before logging what shutdown is waiting on
FORCE_EXIT_AFTER = 7  # seconds before exiting anyway; Docker kills after 10


def install_early_handler() -> None:
    """Before the event loop runs (config, login), just stop like Ctrl+C would."""
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)


def install(loop: asyncio.AbstractEventLoop, close: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Once the loop runs: SIGTERM and SIGINT call `close` and start the watchdog."""
    started = False

    def request(sig: signal.Signals) -> None:
        nonlocal started
        if started:
            return
        started = True
        log.info("Received %s; shutting down", sig.name)
        _start_watchdog(loop)
        loop.create_task(close())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, request, sig)


def _start_watchdog(loop: asyncio.AbstractEventLoop) -> None:
    faulthandler.dump_traceback_later(DIAGNOSE_AFTER + 1, exit=False)
    _daemon_timer(DIAGNOSE_AFTER, lambda: loop.call_soon_threadsafe(_log_pending_tasks))
    _daemon_timer(FORCE_EXIT_AFTER, _force_exit)


def _daemon_timer(seconds: float, action: Callable[[], None]) -> None:
    timer = threading.Timer(seconds, action)
    timer.daemon = True
    timer.start()


def _log_pending_tasks() -> None:
    for task in asyncio.all_tasks():
        log.warning("Shutdown is waiting on task %s:\n%s", task.get_name(), asyncio.format_call_graph(task))


def _force_exit() -> None:
    log.error("Shutdown didn't finish within %d seconds (details above); exiting anyway", FORCE_EXIT_AFTER)
    logging.shutdown()
    os._exit(1)


def _raise_keyboard_interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt
