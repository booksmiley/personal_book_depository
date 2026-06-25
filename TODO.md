# Project status & TODO

_Lean personal library: scan ISBN barcode → register / borrow / return. Website-only,
phone camera as scanner. Brief: `~/Downloads/church-library-brief.md`.
Stack: Python + Flask + stdlib sqlite3 (DB-per-owner). Frontend = zero-build JS in
`static/` + `templates/` (Claude owns frontend; user writes the Python)._

## Done & working
- **Scan → ISBN → metadata**: camera + EAN-13 decode (barcode-detector polyfill),
  ISBN-13 validation (checksum), metadata via Open Library (+ Google Books fallback,
  now with the user's API key for stability). Author resolution is non-fatal.
- **Register flow**: `GET /api/lookup` (online) → "Add to library" → `POST /api/register`
  with two-step duplicate-copy confirm. Frontend sends the already-fetched book so the
  server doesn't re-fetch from the slow API.
- **DB layer (register)**: `find_book_by_isbn`, `add_book`, `add_copy`. `available`
  stored in `books` (not derived).
- **Deploy**: live on Render (gunicorn, `render.yaml`); phone tested over HTTPS.
  Free tier = data EPHEMERAL (no disk).
- **Local run for phone**: `run_local.py` reads `local_config/config.yml` (git-ignored),
  serves HTTPS via an **openssl self-signed cert** (NOT the `cryptography` pkg — it fails
  to build from Rust source here). Data dir is configurable via `BOOK_DATA_DIR`, set to
  `~/.book_depository/data` (outside the repo, safe from git, persistent).
- **Frontend mode selector** (Register / Borrow / Return) with pause-on-hit flow and
  "Scan next book".
- **Borrow / Return flow**: each borrow is an individual loan with a `borrower` label.
  Return = explicit loan selection (scan → list open loans → tap to close). `available`
  updated atomically with the loan in one transaction.
  - Schema: `loans(loan_id, book_id, borrower, borrowed_at, returned_at)`; `returned_at IS NULL` = still out.
  - Routes: `GET /api/book/<isbn>`, `POST /api/borrow/<isbn>`, `POST /api/return/<isbn>`

- **iCloud backup**: `backup_db(conn, path)` writes a consistent SQLite snapshot via
  `conn.backup()` + atomic `os.replace` after every write. `backup_dir` in
  `local_config/config.yml` (blank = off); set via `BOOK_BACKUP_DIR` env var.
  Live DB stays local — only finished snapshots land in iCloud.

## Backlog
### Later
- Add borrower `contact` (phone/email) once secure storage exists.
- Render durable data: paid disk (template in `render.yaml`) or Fly volume.
- Overdue tracking / "who has what" view (derivable from `loans`).
