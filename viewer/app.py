"""Read-only public viewer: the collection page, fed from catalog.json in R2.

No database, no write endpoints, no Litestream. The publish script on the owner's
laptop uploads the catalog snapshot (see docs/viewer_design.md); this app caches it
in memory and serves:

    GET /            the collection page (grid/list/filter/columns)
    GET /api/books   the cached book list + generated_at stamp

Refresh strategy: a conditional GET (If-None-Match with the stored ETag) at most
once per VIEWER_CACHE_TTL seconds — unchanged data costs a 304 with no body. If R2
is unreachable, the last good copy keeps being served, flagged stale.

Local run:  R2_* env vars set -> python viewer/app.py -> http://localhost:8010
Render:     gunicorn viewer.app:app  (second service in render.yaml)
"""

import base64
import hmac
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

# viewer/ lives one level under the project root; the templates, static files and
# the i18n strings are shared with the full app.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from book_depository.i18n import get_strings  # noqa: E402  (imports nothing else)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("viewer")

app = Flask(
    __name__,
    template_folder=str(_ROOT / "templates"),
    static_folder=str(_ROOT / "static"),
)

# --- Data source: the published catalog in R2 (use the READ-ONLY token here) ---
R2_BUCKET = os.environ.get("R2_BUCKET", "")
R2_ENDPOINT = os.environ.get("R2_ENDPOINT", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_KEY = os.environ.get("R2_KEY", "public/catalog.json")
CACHE_TTL = int(os.environ.get("VIEWER_CACHE_TTL", "60"))  # seconds between checks

if not (R2_BUCKET and R2_ENDPOINT and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY):
    log.warning("R2_* env vars incomplete — /api/books will return 503 until set")

# --- Page config (same knobs as the full app) ---
THEME = os.environ.get("BOOK_THEME", "apple")
_VALID_THEMES = {"apple", "win95", "terminal"}
TITLE = os.environ.get("BOOK_TITLE", "Library")
LANG = os.environ.get("BOOK_LANG", "en")
STRINGS = get_strings(LANG)

# Set BOOK_PASSWORD to gate the whole site behind Basic Auth; unset = public.
BOOK_PASSWORD = os.environ.get("BOOK_PASSWORD", "")

# In-memory brute-force throttle, same shape as the full app's: too many failed
# password attempts from one IP inside the window -> 429 until they age out.
_AUTH_FAILS: dict[str, list[float]] = {}
_AUTH_FAIL_WINDOW = 300  # seconds
_AUTH_FAIL_MAX = 20


def _client_ip() -> str:
    # Rightmost X-Forwarded-For entry = the hop the trusted proxy (Render) appended;
    # leftmost entries are attacker-supplied.
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[-1].strip() or "?"
    return request.remote_addr or "?"


def _recent_fails(ip: str) -> list[float]:
    now = time.time()
    fails = [t for t in _AUTH_FAILS.get(ip, []) if now - t < _AUTH_FAIL_WINDOW]
    if fails:
        _AUTH_FAILS[ip] = fails
    else:
        _AUTH_FAILS.pop(ip, None)  # don't retain empty buckets (unbounded growth)
    return fails


@app.before_request
def check_auth():
    if not BOOK_PASSWORD:
        return
    ip = _client_ip()
    if len(_recent_fails(ip)) >= _AUTH_FAIL_MAX:
        return Response("Too many failed attempts — try again shortly.", 429)
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8", errors="replace")
            _, _, password = decoded.partition(":")
            if hmac.compare_digest(password, BOOK_PASSWORD):  # constant-time
                return
        except Exception:
            pass
    fails = _recent_fails(ip)
    fails.append(time.time())
    _AUTH_FAILS[ip] = fails
    return Response(
        "Library access is password-protected.",
        401,
        {"WWW-Authenticate": 'Basic realm="Library"'},
    )


# No CSRF guard here on purpose: this app has no state-changing routes to protect.


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    # Same conservative CSP as the full app: covers load from external https hosts
    # and the terminal theme imports a Google Font, so script/img/style sources stay
    # open; block plugins, framing, and <base> hijacking.
    resp.headers.setdefault(
        "Content-Security-Policy",
        "frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
    )
    if request.is_secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return resp


# --- Catalog cache -----------------------------------------------------------
# One dict guarded by a lock (gunicorn threads share it). "etag" makes the refresh
# a cheap 304 when nothing changed; "stale" marks a copy we couldn't re-validate.
_cache = {"data": None, "etag": None, "checked": 0.0, "stale": False}
_cache_lock = threading.Lock()
_s3 = None


def _client():
    global _s3
    if _s3 is None:
        import boto3  # deferred so the module imports even without boto3 (tests)

        _s3 = boto3.client(
            "s3",
            endpoint_url=R2_ENDPOINT,
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        )
    return _s3


def _refresh() -> None:
    """Fetch catalog.json if it changed since the cached ETag. Raises on failure."""
    from botocore.exceptions import ClientError

    kwargs = {"Bucket": R2_BUCKET, "Key": R2_KEY}
    if _cache["etag"]:
        kwargs["IfNoneMatch"] = _cache["etag"]
    try:
        resp = _client().get_object(**kwargs)
    except ClientError as e:
        status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = str(e.response.get("Error", {}).get("Code", ""))
        if status == 304 or code in ("304", "NotModified"):
            return  # unchanged — the cached copy is still current
        raise
    _cache["data"] = json.loads(resp["Body"].read())
    _cache["etag"] = resp.get("ETag")
    log.info(
        "catalog refreshed: %d books, generated_at=%s",
        len(_cache["data"].get("books", [])),
        _cache["data"].get("generated_at"),
    )


def get_catalog() -> tuple[dict | None, bool]:
    """(catalog, stale). Checks R2 at most once per CACHE_TTL; on failure keeps
    serving the last good copy with stale=True (None if there's no copy yet)."""
    now = time.monotonic()
    with _cache_lock:
        if _cache["data"] is not None and now - _cache["checked"] < CACHE_TTL:
            return _cache["data"], _cache["stale"]
        try:
            _refresh()
            _cache["stale"] = False
        except Exception:
            log.exception("catalog refresh failed")
            _cache["stale"] = True
        _cache["checked"] = now
        return _cache["data"], _cache["stale"]


# --- Routes ------------------------------------------------------------------


@app.get("/")
def index():
    theme = THEME if THEME in _VALID_THEMES else "apple"
    return render_template(
        "viewer.html", theme=theme, title=TITLE, lang=LANG, strings=STRINGS
    )


@app.get("/api/books")
def books():
    catalog, stale = get_catalog()
    if catalog is None:
        return jsonify(error="Catalog temporarily unavailable — try again shortly."), 503
    return jsonify(
        books=catalog.get("books", []),
        generated_at=catalog.get("generated_at"),
        stale=stale,
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8010)
