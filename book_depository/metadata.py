"""ISBN -> book metadata, fetched once and normalized into a single shape.

This module's whole job is to turn an ISBN into ONE consistent `Book` object,
no matter which public API answered. Everything downstream (register / borrow /
return, the DB) depends only on `Book`, never on the raw API JSON. That decoupling
is the point: if an API changes or you add a third source, only this file changes.
"""

import logging
from dataclasses import asdict, dataclass
from enum import Enum

from book_depository.sources import google, openlibrary
from book_depository.sources.douban import fetch_douban_metadata
from book_depository.sources.ects import fetch_ects_metadata
from book_depository.sources.isbnnet import fetch_isbnnet_metadata

log = logging.getLogger(__name__)


class ApiSource(Enum):
    google = "GOOGLE"
    open_lib = "OPEN_LIB"
    douban = "DOUBAN"
    isbnnet = "ISBNNET"
    other = "OTHER"


@dataclass
class Book:
    isbn: str
    title: str
    author: str = ""
    cover_url: str = ""
    publisher: str = ""
    year: str = ""
    language: str = ""
    source: str = ""  # which API(s) answered — handy while debugging coverage gaps

    def to_dict(self) -> dict:
        return asdict(self)


# Fields we fill by combining sources. Once the "core" bibliographic ones are all
# present we stop querying; cover_url/language are filled opportunistically from
# whichever sources we already hit.
_MERGE_FIELDS = ("title", "author", "cover_url", "publisher", "year", "language")
_CORE_FIELDS = ("title", "author", "publisher", "year")


def fetch_book_metadata(isbn: str) -> Book | None:
    """Combine sources in priority order, filling each book's empty fields from the
    next source, until the core fields are all present (or sources run out). This is
    more complete than first-hit: e.g. ISBNnet supplies the correct Traditional
    Chinese title while Google fills a missing publisher or cover."""
    merged: Book | None = None
    contributors: list[str] = []

    # ECTS runs last (strict-throttled), only when faster sources leave core incomplete.
    for source in (
        _from_isbnnet,
        _from_douban,
        _from_open_library,
        _from_google_books,
        _from_ects,
    ):
        try:
            book = source(isbn)
        except Exception as err:  # network OR parse hiccup — skip, try the next source
            log.warning("%s failed for %s: %s", source.__name__, isbn, err)
            continue
        if book is None:
            continue
        if merged is None:
            merged = book
            contributors.append(book.source)
        elif _merge_into(merged, book):
            contributors.append(book.source)
        if _has_core(merged):
            break

    if merged is None:
        log.warning("no metadata found for %s (all sources exhausted)", isbn)
        return None
    merged.source = "+".join(dict.fromkeys(contributors))  # e.g. "ISBNNET+GOOGLE"
    log.info("metadata for %s resolved via %s", isbn, merged.source)
    return merged


def _merge_into(base: Book, extra: Book) -> bool:
    """Fill base's empty fields from extra. Returns True if it contributed anything."""
    filled = False
    for field in _MERGE_FIELDS:
        if not getattr(base, field) and getattr(extra, field):
            setattr(base, field, getattr(extra, field))
            filled = True
    return filled


def _has_core(book: Book) -> bool:
    return all(getattr(book, field) for field in _CORE_FIELDS)


def _from_open_library(isbn: str) -> Book | None:
    data = openlibrary.fetch_open_library_metadata(isbn)
    if data is None:
        return None
    return Book(isbn=isbn, source=ApiSource.open_lib.value, **data)


def _from_google_books(isbn: str) -> Book | None:
    data = google.fetch_google_books_metadata(isbn)
    if data is None:
        return None
    return Book(isbn=isbn, source=ApiSource.google.value, **data)


def _from_isbnnet(isbn: str) -> Book | None:
    data = fetch_isbnnet_metadata(isbn)
    if data is None:
        return None
    return Book(
        isbn=isbn,
        title=data["title"],
        author=data["author"],
        cover_url=_cover_url(isbn),  # ISBNnet covers are unreliable; reuse GB/OL
        publisher=data["publisher"],
        year=data["year"],
        language="zh-Hant",  # Taiwan registry = Traditional Chinese
        source=ApiSource.isbnnet.value,
    )


def _from_douban(isbn: str) -> Book | None:
    data = fetch_douban_metadata(isbn)
    if data is None:
        return None
    return Book(
        isbn=isbn,
        title=data["title"],
        author=data["author"],
        cover_url=_cover_url(isbn),  # Douban blocks hotlinking; use GB or OL instead
        publisher=data["publisher"],
        year=data["year"],
        # No language: Douban lists English/foreign books too and the scraper can't
        # tell, so inferring "zh-Hans" from the source mislabels them. Leave it for
        # Google Books' accurate per-book code (or empty) to fill via the merge.
        source=ApiSource.douban.value,
    )


def _from_ects(isbn: str) -> Book | None:
    data = fetch_ects_metadata(isbn)
    if data is None:
        return None
    return Book(
        isbn=isbn,
        title=data["title"],
        author=data["author"],
        cover_url=_cover_url(isbn),
        publisher=data["publisher"],
        year=data["year"],
        source=ApiSource.other.value,
    )


def _cover_url(isbn: str) -> str:
    """Hotlink-friendly cover URL: Google Books thumbnail, else Open Library."""
    return google.thumbnail(isbn) or openlibrary.cover_url(isbn)


# --- Title search: return a LIST of candidates for the user to pick from ---
_TITLE_SEARCHERS = (
    (google.search_google_books, ApiSource.google),
    (openlibrary.search_open_library, ApiSource.open_lib),
)


def search_by_title(query: str, limit: int = 12) -> list[Book]:
    """Search the API sources by title and return de-duplicated candidates, each with
    an ISBN (so the chosen one can be registered). Google Books and Open Library both
    return result lists; the scrapers don't, so they're not used here."""
    query = query.strip()
    if not query:
        return []
    results: list[Book] = []
    seen: set[str] = set()
    for search, src in _TITLE_SEARCHERS:
        try:
            found = search(query, limit)
        except Exception as err:  # one source failing shouldn't sink the search
            log.warning("%s failed for %r: %s", search.__name__, query, err)
            found = []
        for cand in found:
            isbn = cand.get("isbn", "")
            if not isbn or not cand.get("title") or isbn in seen:
                continue
            seen.add(isbn)
            results.append(Book(source=src.value, **cand))
    return results[:limit]


if __name__ == "__main__":
    print(fetch_book_metadata("9780131103627"))
    print(fetch_book_metadata("9780801020865"))
