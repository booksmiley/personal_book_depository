"""Check connectivity to each metadata source. Run: python check_sources.py [ISBN]

With no argument, hits each source with a known ISBN it should have, so you can see
which sources are reachable from your network right now (ISBNnet / Douban can be slow
or blocked). With an ISBN argument, tests ALL sources against that one book — handy for
debugging why a specific title isn't found.

Note: ISBNnet is only used for Taiwan ISBNs (978 + 957/986/626/627); for any other ISBN
it returns nothing on purpose (no network call), which is why it can look "silent" in a
backfill of mostly non-Taiwan books.
"""

import logging
import os
import sys
from pathlib import Path


def _load_key() -> None:
    """Load GOOGLE_BOOKS_API_KEY from local_config/config.yml before importing metadata
    (so the Google check uses your authenticated quota, not the anonymous one)."""
    cfg = Path(__file__).resolve().parent / "local_config" / "config.yml"
    if not cfg.exists() or os.environ.get("GOOGLE_BOOKS_API_KEY"):
        return
    try:
        import yaml

        data = yaml.safe_load(cfg.read_text()) or {}
        key = data.get("GOOGLE_BOOKS_API_KEY") or data.get("google_books_api_key")
        if key:
            os.environ["GOOGLE_BOOKS_API_KEY"] = key
    except Exception:
        pass


_load_key()  # must run before the metadata import below

from book_depository.douban import fetch_douban_metadata  # noqa: E402
from book_depository.isbnnet import fetch_isbnnet_metadata, is_taiwan_isbn  # noqa: E402
from book_depository.metadata import _from_google_books, _from_open_library  # noqa: E402

# (label, sample ISBN the source should have, callable) — sample used when no ISBN arg.
SOURCES = [
    ("ISBNnet (Taiwan)", "9789575876241", fetch_isbnnet_metadata),   # 21世紀舊約導論
    ("Douban (mainland)", "9787506365437", fetch_douban_metadata),   # 活着 / 余华
    ("Open Library", "9780131103627", _from_open_library),           # K&R C
    ("Google Books", "9780131103627", _from_google_books),           # K&R C
]


def _title(result):
    """Pull a title out of a source result (scrapers return dicts, others return Book)."""
    if result is None:
        return None
    if isinstance(result, dict):
        return result.get("title") or "?"
    return getattr(result, "title", "?")


def check(label: str, isbn: str, fn) -> None:
    try:
        title = _title(fn(isbn))
    except Exception as err:
        print(f"  [FAIL] {label:<18} {isbn}  {type(err).__name__}: {err}")
        return
    if title:
        print(f"  [OK]   {label:<18} {isbn}  {title}")
    else:
        note = ""
        if label.startswith("ISBNnet") and not is_taiwan_isbn(isbn):
            note = "  (not a Taiwan ISBN — skipped by design)"
        print(f"  [MISS] {label:<18} {isbn}  no data{note}")


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    arg_isbn = sys.argv[1].replace("-", "").strip() if len(sys.argv) > 1 else None
    if arg_isbn:
        print(f"Testing every source against {arg_isbn}:\n")
        for label, _, fn in SOURCES:
            check(label, arg_isbn, fn)
    else:
        print("Checking each source against a known ISBN it should have:\n")
        for label, isbn, fn in SOURCES:
            check(label, isbn, fn)


if __name__ == "__main__":
    main()
