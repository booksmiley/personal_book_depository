"""Publish the catalog as one JSON file to Cloudflare R2 for the public viewer.

This is the "sync" in the viewer design (docs/viewer_design.md): read the local
SQLite, build {"generated_at", "books"} and PUT it to public/catalog.json in the
bucket. One object, one atomic PUT — the viewer app on Render polls that file.

    python scripts/publish_catalog.py            # read DB, upload catalog.json
    python scripts/publish_catalog.py --dry-run  # print the JSON, upload nothing

Setup (one time):
  * pip install -r requirements-local.txt   (adds boto3)
  * fill in the `r2:` section of local_config/config.yml (see config.template.yml)

Data dir comes from local_config/config.yml / BOOK_DATA_DIR, same as the app.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# scripts/ live one level under the project root; add it to the path so the
# book_depository package imports work when run as `python scripts/publish_catalog.py`.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# Step 1 (config loading) lives in book_depository/config.py — shared with the other
# scripts and run_local.py. Safe at module top: config.py never touches db.py, so
# BOOK_DATA_DIR can still be set by apply_env() in main() before db.py is imported.
from book_depository.config import apply_env, load_config  # noqa: E402

DEFAULT_OWNER = "lib_admin"

# Mirror of app.py's _EXPORT_COLUMNS: the fields that are safe to publish.
# Deliberately NO loan/borrower data — the public viewer must never see it.
PUBLIC_COLUMNS = (
    "isbn", "title", "author", "publisher", "year", "language", "tags",
    "total_count", "available", "source", "added_at", "cover_url",
)


def r2_settings(cfg: dict) -> dict:
    r2_params = cfg.get("r2", {})
    required_keys = ["bucket", "endpoint", "access_key_id", "secret_access_key"]
    for key in required_keys:
        if not r2_params.get(key):
            sys.exit(f"Missing required R2 config key: {key}")
    if not r2_params.get("key"):
        r2_params["key"] = "public/catalog.json"
    return r2_params


def fetch_books() -> list[dict]:
    # Imported here, not at module top: db.py reads BOOK_DATA_DIR at import time,
    # and apply_env() only sets it once main() runs.
    from book_depository.db import get_all_books, get_db

    conn = get_db(DEFAULT_OWNER)
    try:
        books = get_all_books(conn)
        # Convert sqlite3.Row to dict and filter to PUBLIC_COLUMNS
        cleaned_books = []
        for row in books:
            cleaned_book = {k: row[k] for k in PUBLIC_COLUMNS}
            cleaned_books.append(cleaned_book)
        return cleaned_books
    except Exception as e:
        print(f"Error fetching books: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def build_payload(books: list[dict]) -> bytes:
    """Serialize {"generated_at": <utc iso>, "books": [...]} to UTF-8 JSON bytes."""
    payload = {
        # The viewer shows this as "last synced ..."; UTC so it compares cleanly.
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "books": books,
    }
    # ensure_ascii=False keeps Chinese titles human-readable in the bucket.
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def upload(body: bytes, r2: dict) -> str:
    """PUT the payload to the bucket and return the new ETag."""
    import boto3  # lazy: --dry-run works without boto3 installed

    # R2 speaks the S3 protocol; the endpoint URL points the client at Cloudflare.
    s3 = boto3.client(
        "s3",
        endpoint_url=r2["endpoint"],
        aws_access_key_id=r2["access_key_id"],
        aws_secret_access_key=r2["secret_access_key"],
    )
    resp = s3.put_object(
        Bucket=r2["bucket"],
        Key=r2["key"],
        Body=body,
        ContentType="application/json",
    )
    # The content hash the viewer's If-None-Match polling keys off.
    return resp["ETag"]


# --- Shared with run_local's auto-publisher -----------------------------------

CATALOG_FILENAME = "catalog.json"
STALE_AFTER = 12 * 3600  # seconds; run_local republishes past this age


def local_copy_path() -> Path:
    # Imported lazily: db.py reads BOOK_DATA_DIR at import time (see fetch_books).
    from book_depository.db import DATA_DIR

    return DATA_DIR / CATALOG_FILENAME


def publish(r2: dict) -> tuple[int, str]:
    """Fetch → build → upload → save the local copy. Returns (book count, ETag).

    The local copy is written only AFTER a successful upload, so its mtime marks
    the last SUCCESSFUL publish — if the upload fails (laptop offline), the marker
    stays old and the next staleness check retries instead of waiting 12 h."""
    books = fetch_books()
    body = build_payload(books)
    etag = upload(body, r2)
    local_copy_path().write_bytes(body)
    return len(books), etag


def publish_if_stale(cfg: dict) -> bool:
    """Publish when the local copy is missing or older than STALE_AFTER seconds.
    Returns True if a publish happened."""
    local = local_copy_path()
    if local.exists() and time.time() - local.stat().st_mtime < STALE_AFTER:
        return False
    publish(r2_settings(cfg))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="print the JSON that would be uploaded, upload nothing")
    args = parser.parse_args()

    cfg = load_config()
    apply_env(cfg)  # must run before fetch_books imports book_depository.db
    books = fetch_books()
    body = build_payload(books)

    if args.dry_run:
        print(body.decode("utf-8"))
        print(f"\n[dry-run] {len(books)} books, {len(body)} bytes — nothing uploaded",
              file=sys.stderr)
        return 0

    r2 = r2_settings(cfg)
    etag = upload(body, r2)
    local_copy_path().write_bytes(body)  # manual runs also reset the 12 h timer
    print(f"uploaded {len(books)} books ({len(body)} bytes) "
          f"to {r2['bucket']}/{r2['key']} etag={etag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
