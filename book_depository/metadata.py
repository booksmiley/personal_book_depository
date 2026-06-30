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

from book_depository.douban import fetch_douban_metadata
from book_depository.isbn import isbn13_to_isbn10
from book_depository.isbnnet import fetch_isbnnet_metadata
from book_depository.nlc import fetch_nlc_metadata

log = logging.getLogger(__name__)
GOOGLE_BOOKS_API_KEY = os.environ.get("GOOGLE_BOOKS_API_KEY", "")
# Google Books returns different (sometimes empty) results depending on the caller's
# country; pin it so results are deterministic regardless of where we deploy.
GOOGLE_BOOKS_COUNTRY = os.environ.get("GOOGLE_BOOKS_COUNTRY", "US")
OPEN_LIBRARY_URL = "https://openlibrary.org/isbn/{isbn}.json"
GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
COVER_URL = "https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg"
OPEN_LIBRARY_AUTHORS_URL = "https://openlibrary.org{key}.json"

TIMEOUT = 10  # seconds — never let a slow API hang the request


class ApiSource(Enum):
    google = "GOOGLE"
    open_lib = "OPEN_LIB"
    douban = "DOUBAN"
    isbnnet = "ISBNNET"
    nlc = "NLC"


@dataclass
class Book:
    isbn: str
    title: str
    author: str = ""
    cover_url: str = ""
    publisher: str = ""
    year: str = ""
    source: str = ""  # which API answered — handy while debugging coverage gaps

    def to_dict(self) -> dict:
        return asdict(self)


def fetch_book_metadata(isbn: str) -> Book | None:
    # ISBNnet is first because it's the highest-quality source for Taiwan ISBNs and
    # returns None instantly (no network) for everything else — so it's effectively
    # free for non-Taiwan books while giving Taiwan books their best source first.
    for source in (
        _from_isbnnet,
        _from_douban,
        _from_google_books,
        _from_open_library,
        _from_nlc,
    ):
        try:
            book = source(isbn)
        except requests.RequestException as err:
            log.warning("%s failed for %s: %s", source.__name__, isbn, err)
            book = None
        if book is not None:
            log.info("metadata for %s resolved via %s", isbn, book.source)
            return book
    log.warning("no metadata found for %s (all sources exhausted)", isbn)
    return None


def resolve_open_lib_authors(authors_refs: list) -> str:
    authors = []
    for ref in authors_refs:
        ref_key = ref["key"]
        resp = requests.get(
            OPEN_LIBRARY_AUTHORS_URL.format(key=ref_key), timeout=TIMEOUT
        )
        log.debug("author lookup %s -> HTTP %s", ref_key, resp.status_code)
        authors.append(resp.json().get("name", ""))
    return ", ".join(authors)


def _from_open_library(isbn: str) -> Book | None:
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
        source=ApiSource.google.value,
    )


def _google_books_volume(isbn: str) -> dict | None:
    """Return the first matching volumeInfo dict for an ISBN, or None."""
    params = {"q": f"isbn:{isbn}", "country": GOOGLE_BOOKS_COUNTRY}
    if GOOGLE_BOOKS_API_KEY:
        params["key"] = GOOGLE_BOOKS_API_KEY
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
        source=ApiSource.douban.value,
    )


def _cover_url(isbn: str) -> str:
    """Return a hotlink-friendly cover URL: Google Books thumbnail, else Open Library."""
    try:
        params = {"q": f"isbn:{isbn}", "country": GOOGLE_BOOKS_COUNTRY}
        if GOOGLE_BOOKS_API_KEY:
            params["key"] = GOOGLE_BOOKS_API_KEY
        resp = requests.get(GOOGLE_BOOKS_URL, params=params, timeout=TIMEOUT)
        items = resp.json().get("items")
        if items:
            url = items[0]["volumeInfo"].get("imageLinks", {}).get("thumbnail", "")
            if url:
                return url
    except requests.RequestException:
        pass
    return COVER_URL.format(isbn=isbn)


def _from_nlc(isbn: str) -> Book | None:
    data = fetch_nlc_metadata(isbn)
    if data is None:
        return None
    return Book(
        isbn=isbn,
        title=data["title"],
        author=data["author"],
        cover_url="",  # NLC OPAC has no cover images
        publisher=data["publisher"],
        year=data["year"],
        source=ApiSource.nlc.value,
    )


if __name__ == "__main__":
    print(fetch_book_metadata("9780131103627"))
    print(fetch_book_metadata("9780801020865"))
