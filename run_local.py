"""Run the app locally over HTTPS so a phone on the same WiFi can use the camera.

Reads local_config/config.yml, points the app at your safe local data directory,
loads your Google Books key, and serves with TLS. This is for LOCAL use only —
production on Render still uses gunicorn (see render.yaml).

    python run_local.py

Requires the local-only deps:  pip install -r requirements-local.txt
"""

import os
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent / "local_config" / "config.yml"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"Missing config: {CONFIG_PATH}")
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


def main() -> None:
    cfg = load_config()
    server = cfg.get("server", {})

    # Resolve and create the safe data directory (outside the repo).
    data_dir = os.path.expanduser(cfg.get("data_dir", "~/.book_depository/data"))
    Path(data_dir).mkdir(parents=True, exist_ok=True)

    # IMPORTANT: set env BEFORE importing the app, because db.py and metadata.py
    # read these at import time.
    os.environ["BOOK_DATA_DIR"] = data_dir
    backup_dir = os.path.expanduser(cfg.get("backup_dir", ""))
    if backup_dir:
        os.environ["BOOK_BACKUP_DIR"] = backup_dir
    # Accept either casing in config; the app reads the env var GOOGLE_BOOKS_API_KEY.
    api_key = cfg.get("GOOGLE_BOOKS_API_KEY") or cfg.get("google_books_api_key")
    if api_key:
        os.environ["GOOGLE_BOOKS_API_KEY"] = api_key
    theme = cfg.get("theme", "apple")
    os.environ["BOOK_THEME"] = theme

    from app import app  # noqa: E402 — must come after env is set

    # Decide the TLS context.
    https = server.get("https", {})
    if isinstance(https, bool):  # tolerate a simple `https: true`
        https = {"enabled": https}
    if https.get("enabled", True):
        certfile, keyfile = https.get("certfile"), https.get("keyfile")
        if certfile and keyfile:
            # Resolve ~ and make relative paths relative to the project root, so it
            # works no matter which directory you launch from.
            root = Path(__file__).resolve().parent
            certfile = str(root / os.path.expanduser(certfile))
            keyfile = str(root / os.path.expanduser(keyfile))
            ssl_context = (certfile, keyfile)
        else:
            ssl_context = "adhoc"  # needs the `cryptography` package
    else:
        ssl_context = None

    host = server.get("host", "0.0.0.0")
    port = server.get("port", 8000)
    scheme = "https" if ssl_context else "http"
    print(f"Library data: {data_dir}")
    print(f"On this Mac:  {scheme}://localhost:{port}")
    print(f"On your phone (same WiFi): {scheme}://<this-mac-LAN-IP>:{port}")

    app.run(host=host, port=port, ssl_context=ssl_context, debug=True)


if __name__ == "__main__":
    main()
