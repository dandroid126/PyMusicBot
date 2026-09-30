"""Things that differ between Linux and Windows."""

import asyncio
import signal

from pymusicbot import shutdown
from pymusicbot.commands.owner import _peak_memory_mb


def test_peak_memory_is_reported():
    memory = _peak_memory_mb()
    assert memory is not None and 1 < memory < 10_000


def test_shutdown_falls_back_to_plain_signal_handlers(monkeypatch):
    """Windows has no loop.add_signal_handler; Ctrl+C must still close the bot cleanly."""

    async def scenario():
        loop = asyncio.get_running_loop()
        closes = []

        async def close():
            closes.append(1)

        def unsupported(*args):
            raise NotImplementedError

        monkeypatch.setattr(loop, "add_signal_handler", unsupported)
        monkeypatch.setattr(shutdown, "_start_watchdog", lambda loop: None)
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            shutdown.install(loop, close)
            handler = signal.getsignal(signal.SIGINT)
            assert handler is not previous[signal.SIGINT]
            handler(signal.SIGINT, None)  # what Python does when Ctrl+C arrives
            handler(signal.SIGINT, None)
            for _ in range(5):
                await asyncio.sleep(0)
        finally:
            for sig, original in previous.items():
                signal.signal(sig, original)
        return closes

    assert asyncio.run(scenario()) == [1]
