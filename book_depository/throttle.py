"""Polite per-host throttle for the HTML scrapers (Douban, ISBNnet).

Each scraper calls `wait(host)` before a request so we never hit the same host
faster than MIN_INTERVAL seconds. This smooths a bulk cataloguing session (someone
registering the whole library back-to-back) into a steady trickle, which is what
keeps aggressive sites like Douban from rate-limiting / IP-blocking us.

In-process. Different hosts throttle independently — a Douban hit never delays an
ISBNnet hit. It is NOT shared across multiple instances.

Async version: `wait_async()` reserves the slot the same way but yields the event loop
with `asyncio.sleep` instead of blocking it, so other requests keep running while a
scraper is being paced.
"""

import asyncio
import os
import threading
import time

# Seconds between requests to the same host. Override with SCRAPER_MIN_INTERVAL.
MIN_INTERVAL = float(os.environ.get("SCRAPER_MIN_INTERVAL", "1.0"))

_lock = threading.Lock()
_next_allowed: dict[str, float] = {}


def _reserve(host: str, interval: float) -> float:
    """Reserve this host's next slot; return the delay to wait before using it."""
    with _lock:
        now = time.monotonic()
        scheduled = max(now, _next_allowed.get(host, 0.0))
        _next_allowed[host] = scheduled + interval
        return scheduled - now


def wait(host: str, min_interval: float | None = None) -> None:
    """Block until at least `min_interval` has passed since the last call for `host`."""
    delay = _reserve(host, MIN_INTERVAL if min_interval is None else min_interval)
    if delay > 0:
        time.sleep(delay)


async def wait_async(host: str, min_interval: float | None = None) -> None:
    """Async: pace per-host without blocking the event loop."""
    delay = _reserve(host, MIN_INTERVAL if min_interval is None else min_interval)
    if delay > 0:
        await asyncio.sleep(delay)
