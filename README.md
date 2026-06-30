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
- **Schema migrations**: numbered SQL files in `db_lib/`, applied automatically on
  connection and tracked per-database with `PRAGMA user_version`. Adding a column is
  just dropping a new file — see [Database migrations](#database-migrations).
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
                                       └─ db_lib/  (schema migrations, run on connect)

Borrow mode              ──GET──>  /api/book/<isbn>      (open loans)
                         ──POST─>  /api/borrow/<isbn>    (borrow copy)

Return mode              ──GET──>  /api/book/<isbn>      (list to pick from)
                         ──POST─>  /api/return/<isbn>    (close loan)

Collection mode          ──GET──>  /api/books            (all books)
```

## Database migrations

The schema is defined by numbered SQL files in `db_lib/`, not by code. Each file is
named `NNNN_description.sql`:

```
db_lib/
  __init__.py                          # the runner: run_migrations(conn)
  0001_initial_schema.sql              # books + loans tables
  0002_add_language_column_to_books.sql
```

`run_migrations(conn)` is called in `get_db()` on every connection. It reads the
database's `PRAGMA user_version`, then applies every file whose number is higher, in
order, bumping the version after each. So a database created last month at version 2
automatically picks up a new `0003_*.sql` on its next open, while a brand-new empty
database runs `0001` onward from scratch. No file ever runs twice.

Each file runs inside a single transaction, so the schema change and the version bump
commit together — if a file errors, it rolls back as one and retries cleanly next time.

**To change the schema**, add a new file with the next number and deploy:

```sql
-- db_lib/0003_add_book_tags.sql
ALTER TABLE books ADD COLUMN tags TEXT;
```

SQLite `ADD COLUMN` limits to keep in mind: no `NOT NULL` without a constant `DEFAULT`,
no `UNIQUE`, no `PRIMARY KEY`, and the `DEFAULT` must be a literal. For anything beyond
adding nullable columns (renaming, retyping, adding constraints) use the "12-step"
table-rebuild pattern inside the migration file.

Before applying migrations to real data, take a `backup_db()` snapshot first.

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

## Deploy to Render (with durable data)

Render's free tier has an **ephemeral disk** — the SQLite file is wiped on every
redeploy *and* on every cold start after the service idles. To keep data durable we
run [Litestream](https://litestream.io) inside the container: it restores the database
from object storage on boot and continuously streams changes back up. Render's disk
becomes a throwaway working copy; the **object-storage bucket is the source of truth**.

This needs a free **Cloudflare R2** bucket (S3-compatible object storage; 10 GB free,
no egress fees). The whole setup is a one-time ~15 minutes.

### Step 1 — Create the Cloudflare R2 bucket

1. Sign up at <https://dash.cloudflare.com/sign-up> (free). You'll be asked for a card
   to enable R2, but the free tier won't charge you at this scale.
2. In the dashboard sidebar click **R2** → **Create bucket**.
3. Name it (e.g. `church-library`) and create it. Region: **Automatic** is fine.
4. Note your **endpoint URL**. It's shown on the R2 overview as
   `https://<account-id>.r2.cloudflarestorage.com` (the account-id is a long hex
   string). You'll need this for `R2_ENDPOINT`.

### Step 2 — Create an R2 API token

1. In **R2** → **Manage R2 API Tokens** → **Create API token**.
2. Permissions: **Object Read & Write**. Scope it to your bucket (or all buckets).
3. Create it, then copy the **Access Key ID** and **Secret Access Key** — the secret
   is shown only once. These become `LITESTREAM_ACCESS_KEY_ID` and
   `LITESTREAM_SECRET_ACCESS_KEY`.

### Step 3 — Deploy on Render

1. Push this repo to GitHub (Render deploys from there).
2. At <https://dashboard.render.com> → **New +** → **Blueprint**.
3. Connect your GitHub and pick this repo. Render reads `render.yaml` and sees a Docker
   web service.
4. It will prompt for each `sync: false` env var. Fill them in:

   | Variable | Value |
   |----------|-------|
   | `R2_BUCKET` | your bucket name, e.g. `church-library` |
   | `R2_ENDPOINT` | `https://<account-id>.r2.cloudflarestorage.com` |
   | `LITESTREAM_ACCESS_KEY_ID` | the Access Key ID from step 2 |
   | `LITESTREAM_SECRET_ACCESS_KEY` | the Secret Access Key from step 2 |
   | `BOOK_PASSWORD` | a strong password to gate the public site (recommended) |
   | `GOOGLE_BOOKS_API_KEY` | optional; improves metadata |
   | `BOOK_TITLE` | optional; page heading |
   | `BOOK_THEME` | optional; `apple` / `win95` / `terminal` |

5. **Apply** / **Create**. The first build installs Litestream + the app (a few
   minutes). On boot you'll see `No local DB found — attempting restore from replica…`
   in the logs; on a fresh bucket that's a no-op and the app creates a new DB.

### Step 4 — Verify durability

1. Open the Render URL, register or borrow a book.
2. In Render → **Logs**, confirm a `LEDGER {...}` line appeared for the action, and
   that Litestream logged a replication to R2.
3. In Cloudflare → R2 → your bucket, you should see a `lib_admin/` path with snapshot
   and WAL files.
4. The real test: in Render → **Manual Deploy** → **Clear build cache & deploy** (or
   just wait for an idle spin-down), then reload — your book is still there, restored
   from R2.

### How recovery works

Two independent safety nets:

- **Litestream → R2** is the primary: the live DB is continuously replicated and
  restored on every boot.
- **The `LEDGER` event log** in Render's logs is the backup-to-the-backup. Every
  `book_added` / `copy_added` / `borrowed` / `returned` is a complete JSON record.
  If the bucket were ever lost, replaying these lines in timestamp order rebuilds the
  database. (Render's free-tier log retention is limited; add a log drain later if you
  want indefinite history.)

**Single writer:** the container runs `gunicorn --workers 1` on purpose — Litestream
assumes one writer, and a single worker is plenty for a small library.

### Cost

Free, in practice: R2 free tier (10 GB, no egress) and Render's free web service. The
trade-off of the free Render tier is a slow (~30–60 s) cold start after the service
idles; the first visitor after a quiet spell waits, everyone after is instant.

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
| `BOOK_DATA_DIR` | SQLite data directory (set to `/data` in the Docker image) |
| `BOOK_BACKUP_DIR` | Backup snapshot directory (local iCloud backups; unused on Render) |
| `R2_BUCKET` | Litestream: object-storage bucket name |
| `R2_ENDPOINT` | Litestream: S3 endpoint, e.g. `https://<id>.r2.cloudflarestorage.com` |
| `LITESTREAM_ACCESS_KEY_ID` | Litestream: R2/B2 access key |
| `LITESTREAM_SECRET_ACCESS_KEY` | Litestream: R2/B2 secret key |
