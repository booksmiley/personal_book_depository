"""Re-query every book's metadata via the combined sources and fill in EMPTY fields.

Useful after adding sources or a new field (e.g. `language`): rows registered before
the field/source existed get backfilled. Only empty fields are filled — existing
values (including manual admin edits) are never overwritten.

    python scripts/backfill_metadata.py                  # fill empty fields for every book
    python scripts/backfill_metadata.py --dry-run        # show what would change, write nothing
    python scripts/backfill_metadata.py --fields language  # only backfill specific field(s)
    python scripts/backfill_metadata.py --limit 5        # first 5 books (handy for a test run)

Data dir comes from BOOK_DATA_DIR (defaults to the project's data/ dir), same as the
app. The per-host scraper throttle applies, so this is safe to run over a whole library
(just slow — each book hits the online sources). Take a backup first.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# scripts/ live one level under the project root; add it to the path so the
# book_depository package imports work when run as `python scripts/backfill_metadata.py`.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def _load_config_env() -> None:
    """Pull the Google Books key (and data dir) from local_config/config.yml into the
    environment BEFORE the modules that read them at import time. Using the API key
    means Google Books' generous authenticated rate limit instead of the low anonymous
    one, which is what causes 429s during a bulk backfill. (Needs pyyaml — installed
    via requirements-local.txt.)"""
    cfg_path = _ROOT / "local_config" / "config.yml"
    if not cfg_path.exists():
        return
    try:
        import yaml

        cfg = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception:
        return
    key = cfg.get("GOOGLE_BOOKS_API_KEY") or cfg.get("google_books_api_key")
    if key and not os.environ.get("GOOGLE_BOOKS_API_KEY"):
        os.environ["GOOGLE_BOOKS_API_KEY"] = key
    data_dir = cfg.get("data_dir")
    if data_dir and not os.environ.get("BOOK_DATA_DIR"):
        os.environ["BOOK_DATA_DIR"] = os.path.expanduser(data_dir)


_load_config_env()  # must run before the imports below (they read env at import time)

from book_depository.db import get_all_books, get_db, update_book  # noqa: E402
from book_depository.metadata import fetch_book_metadata  # noqa: E402

DEFAULT_OWNER = "lib_admin"
# Fields the combined sources can supply and that we're willing to backfill.
FILLABLE = ("title", "author", "publisher", "year", "cover_url", "language")

log = logging.getLogger("backfill")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--owner", default=DEFAULT_OWNER)
    ap.add_argument("--dry-run", action="store_true", help="show changes, write nothing")
    ap.add_argument(
        "--fields", nargs="*", default=list(FILLABLE),
        help=f"fields to backfill (default: {' '.join(FILLABLE)})",
    )
    ap.add_argument("--limit", type=int, default=0, help="process at most N books")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    fields = [f for f in args.fields if f in FILLABLE]

    conn = get_db(args.owner)
    try:
        books = get_all_books(conn)
        if args.limit:
            books = books[: args.limit]
        total = len(books)
        updated = unchanged = not_found = 0

        for i, book in enumerate(books, 1):
            isbn = book["isbn"]
            fetched = fetch_book_metadata(isbn)
            if fetched is None:
                not_found += 1
                log.info("[%d/%d] %s — no metadata found online", i, total, isbn)
                continue

            changes = {
                f: getattr(fetched, f)
                for f in fields
                if not book[f] and getattr(fetched, f, "")
            }
            if not changes:
                unchanged += 1
                log.info("[%d/%d] %s — nothing to fill", i, total, isbn)
                continue

            preview = {k: (v[:25] if isinstance(v, str) else v) for k, v in changes.items()}
            log.info("[%d/%d] %s «%s» += %s", i, total, isbn, (book["title"] or "")[:30], preview)
            if not args.dry_run:
                update_book(conn, isbn, changes)
            updated += 1

        tail = " (dry run — nothing written)" if args.dry_run else ""
        print(f"\nDone. {updated} updated, {unchanged} unchanged, {not_found} not found{tail}.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
