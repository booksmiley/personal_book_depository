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

    # Resolve and create the data directory. Default is the project's data/ dir
    # (git-ignored); set data_dir in config.yml to store it elsewhere.
    default_data_dir = str(Path(__file__).resolve().parent / "data")
    data_dir = os.path.expanduser(cfg.get("data_dir") or default_data_dir)
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
    title = cfg.get("title", "Library")
    os.environ["BOOK_TITLE"] = title
    # Trusted local run: admin (edit/delete) is ON by default, no password. Set
    # `admin_open: false` in config.yml to require the admin password locally too.
    admin_open = cfg.get("admin_open", True)
    os.environ["BOOK_ADMIN_OPEN"] = "1" if admin_open else "0"

    from app import app  # noqa: E402 — must come after env is set
    from book_depository.ledger import enable_file_logging  # noqa: E402

    # Persist the reconstruction log locally (date-split files in the data dir). On
    # Render these lines go to the platform logs instead, so this is local-only.
    enable_file_logging(data_dir)

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
