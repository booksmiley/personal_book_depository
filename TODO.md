# Project status & TODO

_Lean church library: scan ISBN → register / borrow / return. Website-only, phone
camera as scanner. Flask + stdlib sqlite3 (DB-per-owner); zero-build JS frontend
(Claude owns frontend; user writes Python). Brief: `~/Downloads/church-library-brief.md`._

## Done & working

- **Scan & input**: EAN-13 barcode (barcode-detector polyfill), Snap still-frame, and
  manual entry. `to_isbn13()` validates/normalises ISBN-10 or -13 → ISBN-13 everywhere.
- **Title search** (register mode): type a title → `GET /api/search?q=` queries Google
  Books + Open Library (candidate lists) → user taps a match → normal register flow
  (`search_by_title` in metadata.py). Works for English and Chinese with the API key.
- **Metadata** (`metadata.py` aggregates per-provider modules under
  `book_depository/sources/`): sources **combined** in priority order
  ISBNnet → Douban → Open Library → Google Books (+ an optional extra source) — each fills
  the previous's empty fields until core fields complete (`source` e.g. `ISBNNET+OPEN_LIB`);
  sets a `language` tag. ISBNnet = Taiwan NCL registry (Traditional Chinese, prefix-gated
  free no-op otherwise). Per-host scraper throttle (`SCRAPER_MIN_INTERVAL`). NLC dropped
  (HTTP/3).
- **Register / borrow / return**: per-book copies; each borrow is one named loan; return
  closes the picked loan; counts updated atomically. `/api/lookup|register|book|borrow|return`.
- **Concurrency**: borrow/return use atomic conditional `UPDATE`s — no over-borrow or
  double-return (tested with 20 racing threads). Register race caught (`IntegrityError`
  → graceful "exists"); migration race fixed (`BEGIN IMMEDIATE` + re-check `user_version`
  under the lock). Server runs `--threads` so a slow metadata lookup (network I/O releases
  the GIL) doesn't block borrow/return — verified a 2s lookup didn't stall a concurrent
  request. (Threads, not async: lookups are I/O-bound, so async would be a big rewrite for
  no gain at this scale.)
- **Backfill tool** (`scripts/backfill_metadata.py`): re-query every book via the combined sources
  to fill EMPTY fields (e.g. `language` on rows registered before it existed); never
  overwrites existing values. `--dry-run` / `--fields` / `--limit`.
- **Source checker** (`scripts/check_sources.py`): probe each metadata source's connectivity vs a
  known ISBN, or all sources against one ISBN. ISBNnet is gated to Taiwan ISBNs
  (957/986/626/627) so it's a silent no-op for others — that's why it can look absent in a
  backfill of mostly non-Taiwan books.
- **Collection**: grid or list; tap to borrow/return inline; pick & reorder columns
  (from DB keys, friendly order, persisted); click a header to sort asc/desc.
- **Admin** (`BOOK_ADMIN_PASSWORD`; `BOOK_ADMIN_OPEN` = trusted local, on by default in
  `run_local.py`): inline edit/delete; constant-time auth; whitelisted fields; logged.
- **Schema migrations** (`db_lib/NNNN_*.sql`): applied per-connection, tracked by
  `PRAGMA user_version`, each in one transaction. Add a column = drop a file, deploy.
- **Durable persistence (Litestream)**: deployed on Render via Docker; replicates SQLite
  → Cloudflare R2, restores on boot (WAL mode, `--workers 1 --threads 8` — one process
  keeps Litestream's single writer; threads serve concurrent requests). Verified live.
- **Reconstruction log (`ledger.py`)**: one JSON `LEDGER` line per mutation → Render logs
  (independent of R2). Local runs also write date-split `YYYY-MM-DD.log` to the data dir.
- **Backups**: `backup_db()` snapshots the SQLite file to `backup_dir` (iCloud) after each
  write; local only (Render relies on Litestream).
- **Local run**: `run_local.py` + `local_config/config.yml`; HTTPS via openssl self-signed
  cert. Data dir defaults to the project's git-ignored `data/`; override in config.
- **Themes / title / language**: `apple` / `win95` / `terminal` (`BOOK_THEME`);
  `BOOK_TITLE`; `BOOK_LANG` = `en` or `zh-Hant` (繁體) — translates the menu +
  borrow/return/register messages (`book_depository/i18n.py`); admin/edit stay English.
- **Security**: site Basic Auth (`BOOK_PASSWORD`) + admin tier; output escaped +
  `https://`-only covers; parameterised SQL incl. whitelisted edit columns.

## Backlog

- **Wrap as a real app** (discuss before building): you can't "wrap" Flask — a wrapper
  packages the JS frontend, so the Python backend has to live somewhere. Three levels:
  1. **PWA** add-to-home (~1 day) — installable, looks native; **data stays on the
     server** (Render/R2). No store, no rewrite.
  2. **Capacitor → Render** — real iOS/Android app (App Store), native camera/barcode;
     webview still calls Render, so **data stays on the server**.
  3. **Self-contained app** — port the backend logic to JS, **on-device SQLite** (iOS
     app container, iCloud-backed; Android app-private). Best fit for *personal
     single-device* use (no shared-source-of-truth tension). ~1–2 weeks; the scrapers
     (Douban/ISBNnet) need Capacitor native HTTP to dodge browser CORS. Don't embed
     Python on mobile (BeeWare/Kivy immature, doesn't fit the web frontend).
  - Also viable without an app: Raspberry-Pi-at-the-venue local server (no cloud);
    remote access to a home server via Tailscale (private) or Cloudflare Tunnel (public).
  - Key tension: a *shared* library needs a shared source of truth, so "fully local data"
    only fully works in a single-station / single-device design.
- **Later**: borrower `contact` field (follow the `language` migration pattern); overdue /
  "who has what" view; log drain for indefinite `LEDGER` retention; watch metadata gaps
  (HK books, mainland titles absent from Douban — add a source if real misses show up).
