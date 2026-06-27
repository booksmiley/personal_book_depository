# Personal Book Depository

A lean open source library system for a small church library. Scan a barcode with your
phone camera → register, borrow, or return books. No app install needed — runs in the
browser over HTTPS.

Stack: Python + Flask + SQLite (one file per owner), zero-build JS frontend.

## What's built

- **Scan**: camera + EAN-13 decode, ISBN-13 validation. Metadata via Douban (Chinese
  books), Google Books, and Open Library in priority order.
- **Snap & manual entry**: freeze a video frame for shaky barcodes, or type/paste an
  ISBN directly.
- **Register**: two-step flow (new book or add a copy), duplicate detection.
- **Borrow**: records a named borrower against a copy; atomic guard prevents over-borrowing.
- **Return**: scan → see open loans → tap the exact loan to close.
- **Collection**: grid or list view of every registered book with availability badges;
  tap any book to borrow or return inline without leaving the page.
- **Themes**: `apple` (default), `win95`, `terminal` — set via env var or config file.
- **iCloud backup**: consistent SQLite snapshot written after every write (config-driven,
  off by default on Render).
- **Password protection**: optional HTTP Basic Auth for public deployments via
  `BOOK_PASSWORD` env var; unset means open access (suitable for trusted LAN use).

## Architecture

```
browser (thin)                     python
──────────────                     ──────────────────────────────────────
camera + decode EAN-13   ──GET──>  /api/lookup/<isbn>
  static/scan.js                     book_depository/isbn.py    (validate)
                                      book_depository/metadata.py (fetch)
                                        └─ douban → google books → open library

Register mode            ──POST─>  /api/register/<isbn>
                                     book_depository/db.py       (write)

Borrow mode              ──GET──>  /api/book/<isbn>      (open loans)
                         ──POST─>  /api/borrow/<isbn>    (borrow copy)

Return mode              ──GET──>  /api/book/<isbn>      (list to pick from)
                         ──POST─>  /api/return/<isbn>    (close loan)

Collection mode          ──GET──>  /api/books            (all books)
```

## Run on your laptop

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py          # http://localhost:8000
```

`localhost` is a secure context, so the camera works on your laptop.

## Run on your phone (local HTTPS)

To scan barcodes from a phone on the same WiFi, you need HTTPS:

```bash
pip install -r requirements-local.txt

# First time: copy the config template and fill in your values
cp local_config/config.template.yml local_config/config.yml
# edit config.yml — set your data_dir, backup_dir, API key, and cert paths

python run_local.py
```

`run_local.py` prints the LAN IP to open on your phone. The first time, your phone
will show a "Not Private" warning — tap through it once. After that it remembers.

### TLS options

| Option | Steps | Phone warning |
|--------|-------|---------------|
| Self-signed (default) | Nothing — just run | Once per device |
| mkcert (no warning) | `brew install mkcert && mkcert <your-lan-ip>`, then set `certfile`/`keyfile` in config.yml | None |

## Deploy to Render

`render.yaml` is included. Push to GitHub, connect to Render, and it deploys
automatically. **Note**: the free tier has no persistent disk — data is wiped on
each redeploy. For a permanent deploy, add a paid disk volume (see `render.yaml`
comments).

Set `BOOK_PASSWORD` in the Render environment dashboard to gate the public URL behind
a password (HTTP Basic Auth — browser shows a native login dialog). Leave it unset for
open LAN-only deployments.

## Config reference

### `local_config/config.yml` (local only, git-ignored)

See `local_config/config.template.yml` for a fully commented template. Key fields:

| Field | Default | Purpose |
|-------|---------|---------|
| `data_dir` | `~/.book_depository/data` | Where the SQLite file lives (outside the repo) |
| `backup_dir` | *(blank)* | iCloud (or any dir) for consistent snapshots; blank = off |
| `google_books_api_key` | *(blank)* | Improves metadata; Open Library is the fallback |
| `title` | `"My Library"` | Page heading and browser tab title |
| `theme` | `apple` | UI theme: `apple` / `win95` / `terminal` |
| `server.https.certfile/keyfile` | *(blank = adhoc)* | Point at mkcert files to avoid the browser warning |

### Environment variables (Render / production)

| Variable | Purpose |
|----------|---------|
| `BOOK_PASSWORD` | Enable Basic Auth; leave unset for open access |
| `BOOK_TITLE` | Page heading (same as `title:` in config) |
| `BOOK_THEME` | UI theme: `apple` / `win95` / `terminal` |
| `GOOGLE_BOOKS_API_KEY` | Google Books API key for improved metadata |
| `BOOK_DATA_DIR` | SQLite data directory |
| `BOOK_BACKUP_DIR` | Backup snapshot directory |
