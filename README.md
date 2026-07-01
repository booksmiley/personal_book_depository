# Personal Book Depository

A lean library app for a personal book collection or a community library. Scan a book's barcode with your phone →
register, borrow, or return it. Runs in the browser, no app installation needed.

Stack: Python + Flask + SQLite, zero-build JS frontend.

## Features

- **Scan / Snap / manual entry / title search** — EAN-13 barcode, still-frame capture
  for shaky shots, type an ISBN (10 or 13), or **search by title** (register mode) and
  pick a match to add.
- **Metadata** — ISBNnet (Taiwan) → Douban (mainland China) → Open Library →
  Google Books (+ optional extra sources), **combined** in priority order (each source
  fills the previous one's gaps) until the core fields are complete; includes a language
  tag. Per-provider modules live in `book_depository/sources/`.
- **Register / borrow / return** — copies tracked per book; each loan names a
  borrower; return closes the exact loan you pick.
- **Collection** — grid or list view; tap a book to borrow/return inline, and choose
  which columns the list shows.
- **Admin mode** — set an admin password to edit or delete records from the
  collection page.
- **Themes & language** — `apple` / `win95` / `terminal`; UI language `en` or
  `zh-Hant` (繁體) for the borrow/return menu and messages, via `BOOK_LANG`.
- **Durable on Render** — Litestream replicates SQLite to object storage; a JSON
  event log is a second safety net (Render logs, plus dated files locally).
- **Auth** — optional site-wide password, plus a separate admin password for edits.

## Architecture

```
browser (thin)                     python
──────────────                     ──────────────────────────────────────
camera + decode EAN-13   ──GET──>  /api/lookup/<isbn>
  static/scan.js                     book_depository/isbn.py    (validate)
                                      book_depository/metadata.py (combine)
                                        └─ sources/: isbnnet → douban → open library → google → …

Register mode            ──POST─>  /api/register/<isbn>
                                     book_depository/db.py       (write)
                                       └─ db_lib/  (schema migrations, run on connect)

Borrow / Return          ──GET──>  /api/book/<isbn>      (open loans)
                         ──POST─>  /api/borrow|return/<isbn>
Collection               ──GET──>  /api/books
Admin (password)         ──PATCH/DELETE─> /api/book/<isbn>
```

## Database migrations

The schema lives in numbered `db_lib/NNNN_*.sql` files, not in code. On every
connection `run_migrations()` checks the DB's `PRAGMA user_version` and applies any
newer files in order (each in one transaction), so new files run automatically and
never twice.

To change the schema, add the next-numbered file and deploy:

```sql
-- db_lib/0003_add_book_tags.sql
ALTER TABLE books ADD COLUMN tags TEXT;
```

SQLite `ADD COLUMN` can't add `NOT NULL` (without a constant default), `UNIQUE`, or
`PRIMARY KEY` — for those, rebuild the table inside the migration. Snapshot real data
before the first run.

After adding a new field or source, backfill existing rows with
`python scripts/backfill_metadata.py` (re-queries the combined sources, fills **empty** fields
only; `--dry-run` to preview).

Check which sources are reachable with `python scripts/check_sources.py` (each source vs a
known ISBN), or `python scripts/check_sources.py <ISBN>` to test all sources against one book.
Note: ISBNnet only answers Taiwan ISBNs (957/986/626/627), so it's silent for others.

## Run on your laptop

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py          # http://localhost:8000
```

`localhost` is a secure context, so the camera works here.

## Run on your phone (local HTTPS)

Phone cameras need HTTPS:

```bash
pip install -r requirements-local.txt
cp local_config/config.template.yml local_config/config.yml   # then edit it
python run_local.py
```

`run_local.py` prints the LAN URL to open on your phone. With a self-signed cert
you tap through a one-time "Not Private" warning; install `mkcert` and point
`certfile`/`keyfile` at its files to skip it.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Offline (no network): unit tests for the ISBN helpers and the metadata combine/merge
(sources mocked), plus integration tests for the DB — borrow/return, the atomic
no-over-borrow guard under 20 racing threads, and migration idempotency.

## Deploy to Render (durable data)

Render's free disk is wiped on redeploy and on idle spin-down, so
[Litestream](https://litestream.io) replicates the SQLite file to a free
**Cloudflare R2** bucket and restores it on boot. One-time setup, ~15 min.

### 1. Create an R2 bucket

1. Sign up at <https://dash.cloudflare.com/sign-up> (a card enables R2; the free
   tier — 10 GB, no egress fees — won't charge you at this scale).
2. **R2** → **Create bucket**, name it e.g. `church-library`.
3. Note the **endpoint** shown for the bucket:
   `https://<account-id>.r2.cloudflarestorage.com` → your `R2_ENDPOINT`.

### 2. Create an R2 API token

**R2** → **Manage R2 API Tokens** → **Create API token** → **Object Read & Write**.
Copy the **Access Key ID** and **Secret Access Key** (the secret shows only once).

### 3. Deploy

1. Push this repo to GitHub.
2. <https://dashboard.render.com> → **New +** → **Blueprint** → pick the repo. Render
   reads `render.yaml` (a Docker service) and prompts for each env var:

   | Variable | Value |
   |----------|-------|
   | `R2_BUCKET` | bucket name, e.g. `church-library` |
   | `R2_ENDPOINT` | `https://<account-id>.r2.cloudflarestorage.com` |
   | `LITESTREAM_ACCESS_KEY_ID` | Access Key ID |
   | `LITESTREAM_SECRET_ACCESS_KEY` | Secret Access Key |
   | `BOOK_PASSWORD` | site password (recommended) |
   | `BOOK_ADMIN_PASSWORD` | unlocks edit/delete (optional) |
   | `GOOGLE_BOOKS_API_KEY` · `BOOK_TITLE` · `BOOK_THEME` | optional |

3. **Apply**. First build takes a few minutes.

### 4. Verify

Register a book, then check: a `LEDGER {...}` line in Render **Logs**, a `lib_admin/`
path in your R2 bucket, and that the book survives a **Manual Deploy** (restored from
R2).

**Notes:** runs `gunicorn --workers 1 --threads 8` — one process keeps Litestream's
single writer, while threads serve requests concurrently so a slow metadata lookup
doesn't block borrow/return. The `LEDGER` log lines are an independent record you can
replay to rebuild the DB if the bucket is ever lost. Free-tier cold starts take
~30–60 s after idle.

## Config reference

### `local_config/config.yml` (local only, git-ignored)

| Field | Default | Purpose |
|-------|---------|---------|
| `data_dir` | *(blank = project `data/`)* | SQLite file + dated ledger logs; set a path to store elsewhere |
| `backup_dir` | *(blank)* | iCloud (or any dir) for snapshots; blank = off |
| `google_books_api_key` | *(blank)* | Improves metadata |
| `title` | `"My Library"` | Page heading / tab title |
| `theme` | `apple` | `apple` / `win95` / `terminal` |
| `server.https.certfile/keyfile` | *(blank = adhoc)* | mkcert files to skip the warning |

### Environment variables (Render / production)

| Variable | Purpose |
|----------|---------|
| `BOOK_PASSWORD` | Site-wide Basic Auth; unset = open access |
| `BOOK_ADMIN_PASSWORD` | Unlocks edit/delete; unset = admin disabled |
| `BOOK_ADMIN_OPEN` | Trusted-local admin with no password (`run_local.py` sets it; never on Render) |
| `BOOK_TITLE` · `BOOK_THEME` | Page heading · UI theme |
| `BOOK_LANG` | Frontend language for the borrow/return menu: `en` (default) or `zh-Hant` |
| `GOOGLE_BOOKS_API_KEY` · `GOOGLE_BOOKS_COUNTRY` | Google Books key · country (default `US`) |
| `SCRAPER_MIN_INTERVAL` | Seconds between scraper hits per host (default `1`) |
| `BOOK_DATA_DIR` · `BOOK_BACKUP_DIR` | SQLite dir (`/data` in Docker) · backup dir |
| `R2_BUCKET` · `R2_ENDPOINT` | Litestream bucket · S3 endpoint |
| `LITESTREAM_ACCESS_KEY_ID` · `LITESTREAM_SECRET_ACCESS_KEY` | R2/B2 credentials |

## License

Licensed under the **GNU General Public License v3.0** — see [LICENSE](LICENSE).
