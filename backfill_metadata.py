"""Re-query every book's metadata via the combined sources and fill in EMPTY fields.

Useful after adding sources or a new field (e.g. `language`): rows registered before
the field/source existed get backfilled. Only empty fields are filled — existing
values (including manual admin edits) are never overwritten.

    python backfill_metadata.py                  # fill empty fields for every book
    python backfill_metadata.py --dry-run        # show what would change, write nothing
    python backfill_metadata.py --fields language  # only backfill specific field(s)
    python backfill_metadata.py --limit 5        # first 5 books (handy for a test run)

Data dir comes from BOOK_DATA_DIR (defaults to the project's data/ dir), same as the
app. The per-host scraper throttle applies, so this is safe to run over a whole library
(just slow — each book hits the online sources). Take a backup first.
"""

import argparse
import asyncio
import logging

from book_depository.db import get_all_books, get_db, update_book
from book_depository.metadata import fetch_book_metadata

DEFAULT_OWNER = "lib_admin"
# Fields the combined sources can supply and that we're willing to backfill.
FILLABLE = ("title", "author", "publisher", "year", "cover_url", "language")

log = logging.getLogger("backfill")


async def main() -> None:
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
            fetched = await fetch_book_metadata(isbn)
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
    asyncio.run(main())
