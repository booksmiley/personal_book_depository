# Public Collection Viewer

A read-only companion app: it shows the collection (grid/list, filter, sortable
columns, column picker) and nothing else — no scan, register, borrow, return, or
admin. It runs as its own small Render service and reads a JSON snapshot published
from the local library.

## How it works

```
laptop (writer)                     Cloudflare R2                 Render (reader)
───────────────                     ─────────────                 ───────────────
full app (run_local.py)             private bucket                viewer (viewer/app.py)
  register/borrow/return             public/catalog.json            conditional GET
        │                                  ▲                        (ETag, ≤ 1/min)
        ▼                                  │                              │
scripts/publish_catalog.py ── PUT ─────────┘                              ▼
  books table → one JSON file                                 GET /           page
                                                              GET /api/books  cached list
```

- **Publishing.** `python scripts/publish_catalog.py` reads the local SQLite and
  PUTs one JSON file — `{"generated_at": …, "books": […]}` — to the bucket. A
  single-object PUT is atomic, so the viewer never sees a half-written file.
  Running it twice is harmless. While the library runs via `run_local.py`, a
  background thread republishes automatically whenever the snapshot is older than
  12 h; the manual run still works anytime for an instant refresh (both reset the
  same timer).
- **The snapshot's local copy** (`<data dir>/catalog.json`) is written only after a
  successful upload, so its mtime marks the last successful publish — a failed
  upload (laptop offline) is retried on the next 15-minute check instead of waiting
  another 12 h.
- **The viewer caches in memory.** It re-checks R2 at most once per
  `VIEWER_CACHE_TTL` seconds using `If-None-Match` with the stored ETag, so
  unchanged data costs a bodyless 304. If R2 is unreachable it keeps serving the
  last good copy, marked stale (⚠ next to the "Updated …" stamp on the page).

## Why a JSON snapshot, not the SQLite file

1. **Privacy** — the DB contains the `loans` table with borrower names; the snapshot
   contains only the public columns (isbn, title, author, publisher, year, language,
   total_count, available, source, added_at, cover_url).
2. **Read-only by construction** — the viewer has no database, no write routes, and
   only a read-only bucket token. A leak of its credentials exposes a catalog that
   is public anyway.
3. **Simplicity** — no Litestream, no Docker, no schema-migration coupling on the
   public service.

## Code layout

| Piece | Where |
|-------|-------|
| Publish script | `scripts/publish_catalog.py` (laptop only) |
| Viewer service | `viewer/app.py` + `viewer/requirements.txt` (flask, gunicorn, boto3) |
| Frontend | `templates/viewer.html`, `static/viewer.js` |
| Shared with the full app | `static/theme-*.css`, `book_depository/i18n.py`, `book_depository/config.py` |

The viewer is deliberately **not** in `render.yaml`: a Blueprint manages every
service in that file, so listing it would entangle the viewer with the full app's
Blueprint (and a second Blueprint from the same repo would try to duplicate the full
app). Deploy it as a standalone manual Web Service instead — manual services ignore
`render.yaml`, so the two deployments never interact.

## Setup

### 1. R2 bucket and tokens

1. Cloudflare dashboard → **R2** → **Create bucket**. Keep it **private**.
2. Create **two** API tokens, each scoped to just this bucket
   (R2 → API Tokens → Manage → Create API token):
   - **Object Read & Write** → used by the laptop publish script.
   - **Object Read only** → used by the viewer.

   Each token yields an **Access Key ID** (32 chars) and a **Secret Access Key**
   (64 chars); the secret is shown only once. Use these S3-style values, *not* the
   Cloudflare "Token value" shown on the same screen.

### 2. Publish from the laptop

Fill the `r2:` section of `local_config/config.yml` (git-ignored) with the
**read-write** pair:

```yaml
r2:
  bucket: "<bucket-name>"
  endpoint: "https://<account-id>.r2.cloudflarestorage.com"
  access_key_id: "<read-write Access Key ID>"
  secret_access_key: "<read-write Secret Access Key>"
  key: "public/catalog.json"
```

Then:

```bash
python scripts/publish_catalog.py --dry-run   # preview the JSON, upload nothing
python scripts/publish_catalog.py             # upload
```

### 3. Render service (manual Web Service, not a Blueprint)

Dashboard → **New + → Web Service** → connect this repo:

| Setting | Value |
|---------|-------|
| Language | Python 3 |
| Build command | `pip install -r viewer/requirements.txt` — **not** the root `requirements.txt` (that one lacks boto3) |
| Start command | `gunicorn --workers 1 --threads 8 --bind 0.0.0.0:$PORT viewer.app:app` |
| Plan | Free |

Environment variables:

| Variable | Required | Purpose |
|----------|----------|---------|
| `R2_BUCKET` | yes | bucket name (no trailing slash) |
| `R2_ENDPOINT` | yes | `https://<account-id>.r2.cloudflarestorage.com` |
| `R2_ACCESS_KEY_ID` | yes | **read-only** token's Access Key ID |
| `R2_SECRET_ACCESS_KEY` | yes | **read-only** token's Secret Access Key |
| `R2_KEY` | no | object path (default `public/catalog.json`) |
| `VIEWER_CACHE_TTL` | no | seconds between R2 checks (default `60`) |
| `BOOK_PASSWORD` | no | Basic-Auth gate for the whole site; unset = public |
| `BOOK_TITLE` · `BOOK_THEME` · `BOOK_LANG` | no | heading · `apple`/`win95`/`terminal` · `en`/`zh-Hant` |
| `PYTHON_VERSION` | no | e.g. `3.12.6` |

### 4. Verify

Open the service URL: the collection renders with an "Updated …" stamp, and the
Render logs show `catalog refreshed: N books, …`. Publish again from the laptop,
click ↻ Reload — the change appears within `VIEWER_CACHE_TTL`.

## Troubleshooting

| Log / symptom | Cause → fix |
|---------------|-------------|
| `ModuleNotFoundError: No module named 'botocore'` | Build used the root `requirements.txt` → set the build command to `viewer/requirements.txt`. |
| `Credential access key has length 0` | `R2_ACCESS_KEY_ID` unset or empty — the variable name must match exactly. |
| `InvalidAccessKeyId` / `SignatureDoesNotMatch` | Wrong values — use the token's S3 key pair, not the Cloudflare "Token value"; both values must come from the same token. |
| `AccessDenied` | Token not scoped to this bucket. |
| `NoSuchBucket` / `NoSuchKey` | Typo in `R2_BUCKET` / `R2_KEY` (no slashes around the bucket name; default key is `public/catalog.json`). |
| "Catalog temporarily unavailable" on the page | The first R2 fetch failed and there is no cache yet — the traceback after `catalog refresh failed` in the Render logs names the real cause. |

## Notes

- Cover images are hotlinked from external hosts (`cover_url`); some hosts
  occasionally block hotlinking. A possible upgrade: have the publish script mirror
  covers into the bucket and rewrite the URLs.
- Render's free tier spins down on idle; a cold start (~30–60 s) fetches a fresh
  catalog on boot.
