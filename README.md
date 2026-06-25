# Personal Book Depository

A lean open source library system for a small church library. Scan a barcode with your
phone camera → register, borrow, or return books. No app install needed — runs in the
browser over HTTPS.

Stack: Python + Flask + SQLite (one file per owner), zero-build JS frontend.

## What's built

- **Scan**: camera + EAN-13 decode, ISBN-13 validation, metadata via Open Library with
  Google Books fallback.
- **Register**: two-step flow (new book or add a copy), duplicate detection.
- **Borrow**: records a named borrower against a copy; atomic guard prevents over-borrowing.
- **Return**: scan → see open loans → tap the exact loan to close. Not FIFO.
- **Collection**: cover-grid view of every registered book with availability badges.
- **iCloud backup**: consistent SQLite snapshot written after every write (config-driven,
  off by default on Render).

## Architecture

```
browser (thin)                     python
──────────────                     ──────────────────────────────────────
camera + decode EAN-13   ──GET──>  /api/lookup/<isbn>
  static/scan.js                     book_depository/isbn.py    (validate)
                                      book_depository/metadata.py (fetch)

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

`localhost` is a secure context, so the camera works on your laptop. The Collection
tab works immediately; for camera scanning you'll need to click "Start camera".

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

## Config reference (`local_config/config.yml`)

See `local_config/config.template.yml` for a fully commented template. Key fields:

| Field | Default | Purpose |
|-------|---------|---------|
| `data_dir` | `~/.book_depository/data` | Where the SQLite file lives (outside the repo) |
| `backup_dir` | *(blank)* | iCloud (or any dir) for consistent snapshots; blank = off |
| `google_books_api_key` | *(blank)* | Improves metadata; Open Library is the fallback |
| `server.https.certfile/keyfile` | *(blank = adhoc)* | Point at mkcert files to avoid the browser warning |
