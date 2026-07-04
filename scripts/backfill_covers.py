"""Repair dead cover URLs: rows whose cover is empty or an Open Library pattern
URL that serves no real image (OL answers those with a blank 1×1 GIF).

For each broken row, retry the same priority chain new registrations use
(metadata._cover_url): Google Books thumbnail → Open Library (existence-checked)
→ Douban's own image. Books where nothing is found get an EMPTY cover_url, so
the frontend shows its no-cover tile and a future backfill can try again.

    python scripts/backfill_covers.py --dry-run     # show what would change
    python scripts/backfill_covers.py --limit 5     # first 5 fixes (test run)
    python scripts/backfill_covers.py               # fix everything

Working URLs from other hosts (Google, Douban) are left untouched. Manual-entry
books (NOISBN-…) are skipped — no external source can know them. Network hits are
throttled per host, so a full run over a few hundred books takes several minutes.
"""

import argparse
import sys
from pathlib import Path

# scripts/ live one level under the project root; add it to the path so the
# book_depository package imports work when run as `python scripts/backfill_covers.py`.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from book_depository.config import apply_env, load_config  # noqa: E402

DEFAULT_OWNER = "lib_admin"
_OL_PATTERN = "covers.openlibrary.org/b/isbn/"


def needs_fix(cover: str) -> bool:
    """Candidates: no cover at all, or an OL pattern URL (possibly a blank pixel)."""
    return not cover or _OL_PATTERN in cover


def find_cover(isbn: str) -> str:
    """Same priority chain as metadata._cover_url, with the Douban page scraped
    lazily only when the cheaper sources come up empty."""
    from book_depository.sources import google, openlibrary
    from book_depository.sources.douban import fetch_douban_metadata

    url = google.thumbnail(isbn)
    if url:
        return url
    if openlibrary.cover_exists(isbn):
        return openlibrary.cover_url(isbn)
    data = fetch_douban_metadata(isbn)
    return (data or {}).get("cover_url", "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change, write nothing")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after N candidate rows (0 = all)")
    args = parser.parse_args()

    cfg = load_config()
    apply_env(cfg)  # must run before importing book_depository.db
    from book_depository.db import get_all_books, get_db, update_book

    conn = get_db(DEFAULT_OWNER)
    try:
        books = get_all_books(conn)
        candidates = [b for b in books if needs_fix(b["cover_url"] or "")]
        if args.limit:
            candidates = candidates[: args.limit]
        print(f"{len(books)} books, {len(candidates)} cover candidates to check")

        fixed = cleared = skipped = 0
        for i, book in enumerate(candidates, 1):
            isbn = book["isbn"]
            old = book["cover_url"] or ""
            if not isbn.isdigit():  # NOISBN-… manual entries: nothing to look up
                skipped += 1
                continue
            new = find_cover(isbn)
            if new == old:
                continue
            tag = "FIX" if new else "CLEAR"
            print(f"[{i}/{len(candidates)}] {tag} {isbn} {book['title'][:30]!r}: "
                  f"{old or '(empty)'} -> {new or '(empty)'}")
            if not args.dry_run:
                update_book(conn, isbn, {"cover_url": new})
            fixed += bool(new)
            cleared += not new
        print(f"done: {fixed} fixed, {cleared} cleared, {skipped} skipped (no ISBN)"
              + (" [dry-run — nothing written]" if args.dry_run else ""))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
