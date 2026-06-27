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
- **Schema migrations** (`db_lib/`): numbered `NNNN_*.sql` files applied in order,
  tracked per-DB via `PRAGMA user_version`. `run_migrations(conn)` runs in `get_db()`
  on every connection (fits DB-per-owner lazy creation; near-free no-op when current).
  Each file runs in one transaction (DDL + version bump commit together, roll back as
  one on failure). `0001` mirrors the shipped schema with `IF NOT EXISTS` so existing
  DBs stamp forward with no data loss. Adding a column = drop a new file, deploy.
  Verified on both fresh and legacy-with-data DBs.
- **Deploy**: live on Render (gunicorn, `render.yaml`); phone tested over HTTPS.
  Free tier = data EPHEMERAL (no disk).
- **Local run for phone**: `run_local.py` reads `local_config/config.yml` (git-ignored),
  serves HTTPS via an **openssl self-signed cert** (NOT the `cryptography` pkg — it fails
  to build from Rust source here). Data dir is configurable via `BOOK_DATA_DIR`, set to
  `~/.book_depository/data` (outside the repo, safe from git, persistent).
- **Frontend mode selector** (Register / Borrow / Return / Collection) with pause-on-hit
  flow and "Scan next book".
- **Borrow / Return flow**: each borrow is an individual loan with a `borrower` label.
  Return = explicit loan selection (scan → list open loans → tap to close). `available`
  updated atomically with the loan in one transaction.
  - Schema: `loans(loan_id, book_id, borrower, borrowed_at, returned_at)`; `returned_at IS NULL` = still out.
  - Routes: `GET /api/book/<isbn>`, `POST /api/borrow/<isbn>`, `POST /api/return/<isbn>`
- **iCloud backup**: `backup_db(conn, path)` writes a consistent SQLite snapshot via
  `conn.backup()` + atomic `os.replace` after every write. `backup_dir` in
  `local_config/config.yml` (blank = off); set via `BOOK_BACKUP_DIR` env var.
  Live DB stays local — only finished snapshots land in iCloud.
- **Snap button**: still-frame barcode decode for shaky-hand / low-light situations.
  Freezes the video frame onto a hidden canvas and runs `BarcodeDetector` on it.
- **Manual ISBN entry**: text field below the camera for typing/pasting an ISBN directly.
- **Douban metadata source**: scrapes `book.douban.com/isbn/{isbn}` for Chinese-language
  books (title, author in Chinese characters). Cover images routed through Google Books /
  Open Library to avoid Douban's hotlink block. Source priority: Douban → Google Books →
  Open Library → NLC (HTTP/3 only, gracefully skipped).
- **UI themes**: three themes selectable via `BOOK_THEME` env var (or `theme:` in
  `local_config/config.yml`):
  - `apple` — clean white cards, SF Pro/system font, blue accents (default)
  - `win95` — silver desktop, 3-D beveled chrome, teal title bars
  - `terminal` — VS Code dark palette, JetBrains Mono, no gimmicks
- **Configurable title**: `BOOK_TITLE` env var / `title:` config key sets the page
  heading and browser tab title. Defaults to `"Library"`.
- **Collection view**: cover grid with availability badges; toggle to list view (title /
  author / year / publisher / status). View preference persisted in `localStorage`.
- **Collection borrow/return**: tap a book card or list row → inline action UI appears
  in place (borrow with name input, return with open-loan picker). No separate page.
- **Security**: stored XSS fixed in `bookInfoHtml` (all fields through `esc()`, cover URL
  validated as `https://`). Optional Basic Auth via `BOOK_PASSWORD` env var for public
  Render deploys; unset = open for local LAN use.

## Backlog
### Mobile app (discussed, not started)
Three realistic paths in order of effort:
1. **PWA** (~1 day): add web app manifest + service worker → "Add to Home Screen" on
   iOS/Android. Looks native, no App Store. Data stays on Render server.
2. **Local device server** (~1 weekend): run Flask on a Raspberry Pi (~$35) or old
   tablet at the church. Phones connect over church WiFi — no cloud at all. No code
   changes needed, just setup.
3. **Capacitor wrapper** (weeks): bundle existing web frontend into a native iOS/Android
   shell; get native barcode APIs and App Store distribution. Still needs a backend for
   shared loan state unless switching to single-device model.

Key design tension: "fully local data" and "any phone can borrow" are in conflict — a
shared library needs a shared source of truth. Single-station (one device = the
terminal) resolves this without a server.

### Later
- Add borrower `contact` (phone/email) once secure storage exists.
- Render durable data: paid disk (template in `render.yaml`) or Fly volume.
- Overdue tracking / "who has what" view (derivable from `loans`).
