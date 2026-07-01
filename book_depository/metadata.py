"""ISBN -> book metadata, fetched once and normalized into a single shape.

This module's whole job is to turn an ISBN into ONE consistent `Book` object,
no matter which public API answered. Everything downstream (register / borrow /
return, the DB) depends only on `Book`, never on the raw API JSON. That decoupling
is the point: if an API changes or you add a third source, only this file changes.
"""

import logging
import os
from dataclasses import asdict, dataclass
from enum import Enum

import requests

from book_depository import throttle
from book_depository.douban import fetch_douban_metadata
from book_depository.ects import fetch_ects_metadata
from book_depository.isbn import isbn10_to_isbn13, isbn13_to_isbn10
from book_depository.isbnnet import fetch_isbnnet_metadata

log = logging.getLogger(__name__)
GOOGLE_BOOKS_API_KEY = os.environ.get("GOOGLE_BOOKS_API_KEY", "")
# Google Books returns different (sometimes empty) results depending on the caller's
# country; pin it so results are deterministic regardless of where we deploy.
GOOGLE_BOOKS_COUNTRY = os.environ.get("GOOGLE_BOOKS_COUNTRY", "US")
OPEN_LIBRARY_URL = "https://openlibrary.org/isbn/{isbn}.json"
GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
# Throttle these APIs per host too (not just the scrapers) — a backfill loops over
# every book and would otherwise burst Google Books into a 429.
_GOOGLE_HOST = "www.googleapis.com"
_OPENLIB_HOST = "openlibrary.org"
COVER_URL = "https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg"
OPEN_LIBRARY_AUTHORS_URL = "https://openlibrary.org{key}.json"

TIMEOUT = 10  # seconds — never let a slow API hang the request


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


def resolve_open_lib_authors(authors_refs: list) -> str:
    authors = []
    for ref in authors_refs:
        ref_key = ref["key"]
        throttle.wait(_OPENLIB_HOST)
        resp = requests.get(
            OPEN_LIBRARY_AUTHORS_URL.format(key=ref_key), timeout=TIMEOUT
        )
        log.debug("author lookup %s -> HTTP %s", ref_key, resp.status_code)
        authors.append(resp.json().get("name", ""))
    return ", ".join(authors)


def _from_open_library(isbn: str) -> Book | None:
    throttle.wait(_OPENLIB_HOST)
    resp = requests.get(OPEN_LIBRARY_URL.format(isbn=isbn), timeout=TIMEOUT)
    log.debug("open library %s -> HTTP %s", isbn, resp.status_code)
    if resp.status_code == 404:
        return None
    data = resp.json()
    # Author resolution is an extra network call (one per author). It's a
    # nice-to-have, so a failure here must NOT sink the whole book — degrade to
    # an empty author instead of letting the exception bubble up and null the result.
    try:
        authors = resolve_open_lib_authors(authors_refs=data.get("authors", []))
    except requests.RequestException as err:
        log.warning("author resolution failed for %s: %s", isbn, err)
        authors = ""
    return Book(
        isbn=isbn,  # thread in the arg — it isn't in the response body
        title=data.get("title", ""),
        author=authors,
        cover_url=COVER_URL.format(isbn=isbn),
        publisher=data.get("publishers", [""])[0],  # OL: list, not a string
        year=data.get("publish_date", ""),  # OL: publish_date, not publishedDate
        source=ApiSource.open_lib.value,
    )


def _from_google_books(isbn: str) -> Book | None:
    info = _google_books_volume(isbn)
    # Some volumes are indexed only under the ISBN-10. Retry with that form before
    # giving up (free helper, no network unless the first query missed).
    if info is None:
        alt = isbn13_to_isbn10(isbn)
        if alt:
            info = _google_books_volume(alt)
    if info is None:
        return None
    return Book(
        isbn=isbn,  # thread in the arg — volumeInfo has no isbn field
        title=info.get("title", ""),
        author=", ".join(info.get("authors", [])),  # Google: plain name strings
        cover_url=info.get("imageLinks", {}).get("thumbnail", ""),
        publisher=info.get("publisher", ""),
        year=info.get("publishedDate", ""),
        language=info.get("language", ""),  # ISO code, e.g. "en", "zh"
        source=ApiSource.google.value,
    )


def _google_books_volume(isbn: str) -> dict | None:
    """Return the first matching volumeInfo dict for an ISBN, or None."""
    params = {"q": f"isbn:{isbn}", "country": GOOGLE_BOOKS_COUNTRY}
    if GOOGLE_BOOKS_API_KEY:
        params["key"] = GOOGLE_BOOKS_API_KEY
    throttle.wait(_GOOGLE_HOST)
    resp = requests.get(GOOGLE_BOOKS_URL, params=params, timeout=TIMEOUT)
    items = resp.json().get("items")
    if not items:
        return None
    return items[0]["volumeInfo"]


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
    """Return a hotlink-friendly cover URL: Google Books thumbnail, else Open Library."""
    try:
        params = {"q": f"isbn:{isbn}", "country": GOOGLE_BOOKS_COUNTRY}
        if GOOGLE_BOOKS_API_KEY:
            params["key"] = GOOGLE_BOOKS_API_KEY
        throttle.wait(_GOOGLE_HOST)
        resp = requests.get(GOOGLE_BOOKS_URL, params=params, timeout=TIMEOUT)
        items = resp.json().get("items")
        if items:
            url = items[0]["volumeInfo"].get("imageLinks", {}).get("thumbnail", "")
            if url:
                return url
    except requests.RequestException:
        pass
    return COVER_URL.format(isbn=isbn)


# --- Title search: return a LIST of candidates for the user to pick from ---
OPEN_LIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"


def search_by_title(query: str, limit: int = 12) -> list[Book]:
    """Search the API sources by title and return de-duplicated candidates, each with
    an ISBN (so the chosen one can be registered). Google Books and Open Library both
    return result lists; the scrapers don't, so they're not used here."""
    query = query.strip()
    if not query:
        return []
    results: list[Book] = []
    seen: set[str] = set()
    for search in (_title_google_books, _title_open_library):
        try:
            found = search(query, limit)
        except Exception as err:  # one source failing shouldn't sink the search
            log.warning("%s failed for %r: %s", search.__name__, query, err)
            found = []
        for book in found:
            if not book.isbn or not book.title or book.isbn in seen:
                continue
            seen.add(book.isbn)
            results.append(book)
    return results[:limit]


def _title_google_books(query: str, limit: int) -> list[Book]:
    params = {
        "q": query,  # general query: forgiving if the user types "title author"
        "country": GOOGLE_BOOKS_COUNTRY,
        "maxResults": min(limit, 20),
    }
    if GOOGLE_BOOKS_API_KEY:
        params["key"] = GOOGLE_BOOKS_API_KEY
    throttle.wait(_GOOGLE_HOST)
    resp = requests.get(GOOGLE_BOOKS_URL, params=params, timeout=TIMEOUT)
    out = []
    for item in resp.json().get("items", []) or []:
        info = item.get("volumeInfo", {})
        out.append(
            Book(
                isbn=_isbn_from_google(info.get("industryIdentifiers", [])),
                title=info.get("title", ""),
                author=", ".join(info.get("authors", [])),
                cover_url=info.get("imageLinks", {}).get("thumbnail", ""),
                publisher=info.get("publisher", ""),
                year=info.get("publishedDate", ""),
                language=info.get("language", ""),
                source=ApiSource.google.value,
            )
        )
    return out


def _isbn_from_google(identifiers: list) -> str:
    ids = {i.get("type"): i.get("identifier", "") for i in identifiers}
    if ids.get("ISBN_13"):
        return ids["ISBN_13"]
    if ids.get("ISBN_10"):
        return isbn10_to_isbn13(ids["ISBN_10"])
    return ""


def _title_open_library(query: str, limit: int) -> list[Book]:
    params = {
        "q": query,  # general query (matches title/author), forgiving like Google
        "limit": limit,
        "fields": "title,author_name,first_publish_year,isbn,publisher,cover_i",
    }
    throttle.wait(_OPENLIB_HOST)
    resp = requests.get(OPEN_LIBRARY_SEARCH_URL, params=params, timeout=TIMEOUT)
    out = []
    for doc in resp.json().get("docs", []) or []:
        isbns = doc.get("isbn", []) or []
        isbn13 = next((x for x in isbns if len(x) == 13 and x.startswith("978")), "")
        cover = (
            f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-M.jpg"
            if doc.get("cover_i")
            else ""
        )
        out.append(
            Book(
                isbn=isbn13,
                title=doc.get("title", ""),
                author=", ".join((doc.get("author_name") or [])[:3]),
                cover_url=cover,
                publisher=(doc.get("publisher") or [""])[0],
                year=str(doc.get("first_publish_year") or ""),
                source=ApiSource.open_lib.value,
            )
        )
    return out


if __name__ == "__main__":
    print(fetch_book_metadata("9780131103627"))
    print(fetch_book_metadata("9780801020865"))
