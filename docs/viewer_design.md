# Public Collection Viewer — System Design

A second, public-facing web app on Render that shows **only the collection view**
(grid/list, filter, column picker — no scan, register, borrow, return, or admin).
The source of truth is the **local** full app; data reaches the viewer through the
existing Cloudflare R2 bucket.

## Data flow

```
laptop (writer)                     Cloudflare R2                Render (reader)
────────────────────                ─────────────                ────────────────
full app (run_local.py)             bucket: church-library       viewer app (tiny Flask)
  register/borrow/return              lib_admin/   ← existing      boot / TTL expiry:
        │                             Litestream path (full app)     GET public/catalog.json
        ▼                                                            (If-None-Match: <etag>)
scripts/publish_catalog.py  ──PUT──►  public/catalog.json  ──GET──►  in-memory cache
  read local SQLite                   (one small JSON file)              │
  (reuse get_all_books)                                                  ▼
  add generated_at stamp                                     GET /            collection page
                                                             GET /api/books   from cache
```

1. The full app runs **locally** and builds up the database (register, borrow, return).
2. A local **publish script** exports the books table to one JSON file and uploads it
   to R2.
3. The **viewer app** on Render reads that JSON file and serves the collection page.

## Key decision: sync a JSON snapshot, not the SQLite file

Litestream (local replicates → viewer restores) was considered and rejected for the
viewer:

1. **Privacy** — the SQLite file contains the `loans` table with borrower names. A
   public viewer should never even possess that data. A published snapshot contains
   only what is chosen for publication.
2. **Read-only by construction** — the viewer has no database, no write endpoints,
   and only a read-only R2 token. Nothing to lock down because nothing is writable.
3. **Simplicity** — Litestream v0.3 has no live read replica, so the viewer would
   need awkward restore-on-interval logic, the Docker/Litestream machinery, and
   schema-migration coupling. A JSON file needs none of that.

The export shape already exists: `/api/export.json` produces the book list with
`_EXPORT_COLUMNS` (isbn, title, author, publisher, year, language, total_count,
available, source, added_at, cover_url) — borrower data already excluded.

## How the sync works

The "sync" is not a continuous replication process — it is a small script that does
one thing: **read the database, write one JSON file to R2 over HTTPS**. Run the
script = sync happens.

**Step 1 — read.** Open `data/lib_admin.sqlite` the same way the app does
(`get_db(...)` → `get_all_books(conn)`). Books only; no loan data.

**Step 2 — serialize.**

```python
payload = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "books": [dict(b) for b in books],
}
body = json.dumps(payload, ensure_ascii=False)
```

`generated_at` lets the viewer show "last synced 2 hours ago".

**Step 3 — upload.** R2 speaks the S3 protocol, so `boto3` pointed at Cloudflare:

```python
s3 = boto3.client(
    "s3",
    endpoint_url="https://<account-id>.r2.cloudflarestorage.com",  # same R2_ENDPOINT as litestream.yml
    aws_access_key_id=...,      # R2 API token, read-write (laptop only)
    aws_secret_access_key=...,
)
s3.put_object(Bucket="church-library", Key="public/catalog.json",
              Body=body.encode("utf-8"), ContentType="application/json")
```

Under the hood: a single authenticated HTTPS `PUT`. The credentials are the same
kind of token Litestream uses (Access Key ID + Secret).

Properties that make this safe:

- **Atomic** — an R2 `put_object` either fully replaces the object or doesn't
  happen. The viewer can never read a half-written file.
- **Change detection** — every upload gets a new **ETag** (content hash). The viewer
  polls with `If-None-Match: <last etag>`; R2 answers `304 Not Modified` (no body)
  until the content changes, then sends the new JSON once.
- **Idempotent** — running the script twice is harmless. When in doubt, run it again.

### Trigger options (escalating; start with the first)

| Option | How | Trade-off |
|--------|-----|-----------|
| Manual | run `python scripts/publish_catalog.py` after a library session | simplest; catalog only changes when you change it, so you know when to run it |
| Timer | macOS `launchd` job, e.g. hourly | covers forgetfulness; redundant uploads are harmless (viewer still 304s) |
| Hook | app calls publish after each register/edit/delete | fully automatic, but the app then needs network + credentials at request time; a Cloudflare hiccup interrupts library work. Save for later, if ever |

The viewer is only as fresh as the last run — by design. The laptop is the writer of
record; the public site is a snapshot of it.

## Repo layout — same repo, second service

The viewer lives in this repo; no new repo. Two reasons make this the natural fit:

1. **The publish script needs this repo anyway** — it imports `get_db` /
   `get_all_books` from `book_depository/db.py`. Splitting repos would mean
   duplicating or packaging the DB layer.
2. **Render deploys multiple services from one repo** — the Blueprint
   (`render.yaml`) simply lists a second `web` service pointing at the same repo
   with its own start command.

```
personal_book_depository/
├── app.py                       # full app (unchanged)
├── book_depository/             # shared: db.py used by app AND publish script
├── scripts/publish_catalog.py   # NEW — reads DB, uploads catalog.json
├── viewer/
│   ├── app.py                   # NEW — viewer Flask app (no DB, no SQLite)
│   └── requirements.txt         # flask, gunicorn, boto3 — nothing else
├── templates/viewer.html        # NEW
├── static/viewer.js             # NEW
├── static/theme-*.css           # reused as-is by both apps
└── render.yaml                  # two services
```

What is reused without copying: the theme CSS files, `book_depository/db.py` (by the
publish script), and the ISBN/i18n helpers if ever needed. The Basic-Auth +
security-headers logic (~40 lines in `app.py`) can either be copied into
`viewer/app.py` (simplest) or extracted into a shared `book_depository/webutil.py`
that both apps import (nicer; a good refactoring exercise).

What is deliberately **not** shared: `scan.html` / `scan.js`. The viewer gets its own
trimmed template and JS instead of `if (readonly)` flags sprinkled through the full
app — the public page ships zero dead code, and the full app stays untouched.

A "read-only mode" flag on the existing app was rejected: `app.py`'s `/api/books`
reads SQLite, so flag-mode would still drag the whole DB + Litestream machinery onto
the public service — exactly what the JSON-snapshot design avoids.

**Deployment note:** the viewer is *not* listed in `render.yaml`. A Blueprint manages
every service in that file, so adding the viewer there would make the existing
church-library Blueprint create a viewer on its next sync — and a second Blueprint
from the same repo would try to create a duplicate church library. The viewer is a
standalone **manual Web Service** instead (settings under "Render deployment" below);
manual services ignore `render.yaml` entirely, so the two deployments never interact.

If the viewer ever needs to become standalone later, `viewer/` is self-contained
enough to extract in minutes.

## Components

### 1. Publish script — `scripts/publish_catalog.py` (local, Python)

Reads the local DB with the existing `get_db`/`get_all_books`, builds
`{"generated_at", "books"}`, PUTs to `public/catalog.json` with boto3.

### 2. R2 layout and credentials

Same bucket, two prefixes with strictly separated tokens:

| Path | Writer | Reader | Token |
|------|--------|--------|-------|
| `lib_admin/` | Litestream (deployed full app) | Litestream | existing read-write |
| `public/catalog.json` | publish script (laptop) | viewer | script: read-write · viewer: **new read-only token** |

If the viewer's credentials ever leak, an attacker can read a catalog that is public
anyway — and nothing else. For even harder separation, use a second bucket for
`public/`.

> **As built:** the snapshot lives in its own dedicated bucket,
> `personal-book-catalog` (the harder-separation option above), not in the
> Litestream bucket. Same two-token model: laptop = read-write, viewer = read-only.

### 3. Viewer service (Python, ~80 lines of Flask)

No SQLite, no Litestream, no Docker — plain `runtime: python` on Render with
gunicorn.

- On startup and whenever the cache is older than a TTL (60 s is fine), fetch
  `catalog.json` from R2 with a conditional GET (`If-None-Match` + stored ETag).
- `GET /` → collection page; `GET /api/books` → cached list; nothing else exists.
- Keep the `BOOK_PASSWORD` Basic Auth pattern and the security-headers hook from
  `app.py`; drop the CSRF guard (no state-changing routes).
- R2 unreachable → serve the stale cache with a "last synced …" note. No cache at
  all (cold start + R2 down) → friendly 503 page.
- Render free tier's spin-down helps: every cold start naturally fetches a fresh
  catalog.

### 4. Frontend (`viewer.html` + `viewer.js`)

A trimmed collection page: grid/list toggle, title/author filter, column picker, and
cover grid from `scan.js` — with borrow/return tap actions, the camera, and admin
prompts removed. Reuses the three theme CSS files unchanged, plus a "last synced"
indicator fed by `generated_at`. New files rather than flags inside `scan.html`, so
the public page ships zero dead code.

### 5. Render deployment (manual Web Service, not a Blueprint)

Dashboard: **New + → Web Service** → connect this repo → branch `main`:

| Setting | Value |
|---------|-------|
| Language | Python 3 (set env var `PYTHON_VERSION=3.12.6`) |
| Build command | `pip install -r viewer/requirements.txt` |
| Start command | `gunicorn --workers 1 --threads 8 --bind 0.0.0.0:$PORT viewer.app:app` |
| Plan | Free |

Env vars: `R2_BUCKET` (`personal-book-catalog`), `R2_ENDPOINT`, `R2_ACCESS_KEY_ID` /
`R2_SECRET_ACCESS_KEY` (**the read-only token**), optional `BOOK_PASSWORD` /
`BOOK_TITLE` / `BOOK_THEME` / `BOOK_LANG`.

Optional: Settings → Build & Deploy → **Build filters**, include paths `viewer/**`,
`templates/viewer.html`, `static/viewer.js`, `static/theme-*.css`,
`book_depository/i18n.py` — then church-lib-only commits don't redeploy the viewer.

## Data contract — `public/catalog.json`

```json
{
  "generated_at": "2026-07-03T10:15:00+00:00",
  "books": [
    {
      "isbn": "9789571234567",
      "title": "…", "author": "…", "publisher": "…", "year": "…",
      "language": "…", "total_count": 2, "available": 1,
      "source": "…", "added_at": "…", "cover_url": "https://…"
    }
  ]
}
```

## Open considerations

- **Two writers** — today the *deployed* full app on Render is the writer of record
  (Litestream → `lib_admin/`); this design makes the *local* app the writer. If both
  keep accepting registrations, the catalogs fork (separate paths, nothing breaks —
  but the viewer only shows the local one). The design assumes "local becomes the
  real library; the Render full app retires."
- **Covers** — `cover_url` points at external hosts (Google Books, Douban…); Douban
  sometimes blocks hotlinking. Fine for v1 (same behavior as the full app). v2
  option: the publish script mirrors cover images into `public/covers/` and rewrites
  the URLs, making the viewer fully self-contained.
- **Static-site alternative** — the collection UI is entirely client-side, so
  catalog.json + HTML on Cloudflare Pages (no Render, no cold starts) would also
  work. The Flask viewer buys the password gate and server-side caching; if the
  password requirement is ever dropped, static is the cheaper end state.

## Build order

1. **User**: `scripts/publish_catalog.py` — read DB, build `{"generated_at",
   "books"}`, boto3 PUT to R2. Verify the object in the Cloudflare dashboard.
2. **User**: create the read-only R2 token.
3. **User**: `viewer/app.py` — cached fetch (ETag + TTL) and the two routes.
4. **Claude**: `viewer.html` / `viewer.js` / theme CSS reuse.
5. **User**: second service in `render.yaml`, deploy, verify end-to-end:
   publish locally → refresh the viewer → book appears.
