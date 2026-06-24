"""ISBN -> book metadata, fetched once and normalized into a single shape.

This module's whole job is to turn an ISBN into ONE consistent `Book` object,
no matter which public API answered. Everything downstream (register / borrow /
return, the DB) depends only on `Book`, never on the raw API JSON. That decoupling
is the point: if an API changes or you add a third source, only this file changes.
"""

from dataclasses import asdict, dataclass
from enum import Enum

import requests

OPEN_LIBRARY_URL = "https://openlibrary.org/isbn/{isbn}.json"
GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
COVER_URL = "https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg"
OPEN_LIBRARY_AUTHORS_URL = "https://openlibrary.org{key}.json"

TIMEOUT = 6  # seconds — never let a slow API hang the request


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
    for source in (_from_open_library, _from_google_books):
        try:
            book = source(isbn)
        except requests.RequestException:
            book = None
        if book is not None:
            return book
    return None


def resolve_open_lib_authors(authors_refs: str):
    authors = []
    for ref in authors_refs:
        ref_key = ref["key"]
        resp = requests.get(
            OPEN_LIBRARY_AUTHORS_URL.format(key=ref_key), timeout=TIMEOUT
        )
        authors.append(resp.json().get("name", ""))
    return ", ".join(authors)


def _from_open_library(isbn: str) -> Book | None:
    resp = requests.get(OPEN_LIBRARY_URL.format(isbn=isbn), timeout=TIMEOUT)
    if resp.status_code == 404:
        return None
    data = resp.json()
    authors = resolve_open_lib_authors(authors_refs=data.get("authors", []))
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
    resp = requests.get(GOOGLE_BOOKS_URL.format(isbn=isbn), timeout=TIMEOUT)
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
