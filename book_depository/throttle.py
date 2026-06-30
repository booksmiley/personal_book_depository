"""Polite per-host throttle for the HTML scrapers (Douban, ISBNnet).

Each scraper calls `wait(host)` before a request so we never hit the same host
faster than MIN_INTERVAL seconds. This smooths a bulk cataloguing session (someone
registering the whole library back-to-back) into a steady trickle, which is what
keeps aggressive sites like Douban from rate-limiting / IP-blocking us.

In-process and thread-safe. With gunicorn `--workers 1` that single process is the
whole server, so this is sufficient; it is NOT shared across multiple instances.
Different hosts throttle independently — a Douban hit never delays an ISBNnet hit.
"""

import os
import threading
import time

# Seconds between requests to the same host. Override with SCRAPER_MIN_INTERVAL.
MIN_INTERVAL = float(os.environ.get("SCRAPER_MIN_INTERVAL", "1.0"))

_lock = threading.Lock()
_next_allowed: dict[str, float] = {}


def wait(host: str, min_interval: float | None = None) -> None:
    """Block until at least `min_interval` has passed since the last call for `host`."""
    interval = MIN_INTERVAL if min_interval is None else min_interval
    # Reserve this host's slot under the lock, then sleep OUTSIDE it so a slow request
    # to one host never blocks callers targeting a different host.
    with _lock:
        now = time.monotonic()
        scheduled = max(now, _next_allowed.get(host, 0.0))
        _next_allowed[host] = scheduled + interval
        delay = scheduled - now
    if delay > 0:
        time.sleep(delay)
