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

log = logging.getLogger(__name__)
GOOGLE_BOOKS_API_KEY = os.environ.get("GOOGLE_BOOKS_API_KEY", "")
OPEN_LIBRARY_URL = "https://openlibrary.org/isbn/{isbn}.json"
GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
COVER_URL = "https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg"
OPEN_LIBRARY_AUTHORS_URL = "https://openlibrary.org{key}.json"

TIMEOUT = 10  # seconds — never let a slow API hang the request


class ApiSource(Enum):
    google = "GOOGLE"
    open_lib = "OPEN_LIB"


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
    for source in (_from_google_books, _from_open_library):
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
    params = {"q": f"isbn:{isbn}"}
    if GOOGLE_BOOKS_API_KEY:
        params["key"] = GOOGLE_BOOKS_API_KEY
    resp = requests.get(GOOGLE_BOOKS_URL, params=params, timeout=TIMEOUT)
    data = resp.json()
    items = data.get("items")
    if not items:
        return None
    info = items[0]["volumeInfo"]
    return Book(
        isbn=isbn,  # thread in the arg — volumeInfo has no isbn field
        title=info.get("title", ""),
        author=", ".join(info.get("authors", [])),  # Google: plain name strings
        cover_url=info.get("imageLinks", {}).get("thumbnail", ""),
        publisher=info.get("publisher", ""),
        year=info.get("publishedDate", ""),
        source=ApiSource.google.value,
    )


if __name__ == "__main__":
    print(fetch_book_metadata("9780131103627"))
    print(fetch_book_metadata("9780801020865"))
