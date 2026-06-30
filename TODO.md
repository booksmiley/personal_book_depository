# Project status & TODO

_Lean church library: scan ISBN → register / borrow / return. Website-only, phone
camera as scanner. Flask + stdlib sqlite3 (DB-per-owner); zero-build JS frontend
(Claude owns frontend; user writes Python). Brief: `~/Downloads/church-library-brief.md`._

## Done & working

- **Scan & input**: EAN-13 barcode (barcode-detector polyfill), Snap still-frame, and
  manual entry. `to_isbn13()` validates/normalises ISBN-10 or -13 → ISBN-13 everywhere.
- **Metadata** (`metadata.py`): sources **combined** in priority order
  ISBNnet → Douban → Open Library → Google Books — each fills the previous's empty
  fields until core fields complete (`source` e.g. `ISBNNET+OPEN_LIB`); sets a `language`
  tag. ISBNnet = Taiwan NCL registry (Traditional Chinese, prefix-gated free no-op
  otherwise). Per-host scraper throttle (`SCRAPER_MIN_INTERVAL`). NLC dropped (HTTP/3).
- **Register / borrow / return**: per-book copies; each borrow is one named loan; return
  closes the picked loan; counts updated atomically. `/api/lookup|register|book|borrow|return`.
- **Collection**: grid or list; tap to borrow/return inline; pick & reorder columns
  (from DB keys, friendly order, persisted); click a header to sort asc/desc.
- **Admin** (`BOOK_ADMIN_PASSWORD`; `BOOK_ADMIN_OPEN` = trusted local, on by default in
  `run_local.py`): inline edit/delete; constant-time auth; whitelisted fields; logged.
- **Schema migrations** (`db_lib/NNNN_*.sql`): applied per-connection, tracked by
  `PRAGMA user_version`, each in one transaction. Add a column = drop a file, deploy.
- **Durable persistence (Litestream)**: deployed on Render via Docker; replicates SQLite
  → Cloudflare R2, restores on boot (WAL mode, `--workers 1`). Verified live.
- **Reconstruction log (`ledger.py`)**: one JSON `LEDGER` line per mutation → Render logs
  (independent of R2). Local runs also write date-split `ledger-YYYY-MM-DD.log` to the data dir.
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

- **Mobile app** (not started): PWA add-to-home (~1 day) / Raspberry-Pi-at-church local
  server (no cloud) / Capacitor native wrap. Tension: a shared library needs a shared
  source of truth, so "fully local" implies a single-station design.
- **Later**: borrower `contact` field (follow the `language` migration pattern); overdue /
  "who has what" view; log drain for indefinite `LEDGER` retention; watch metadata gaps
  (HK books, mainland titles absent from Douban — add a source if real misses show up).
