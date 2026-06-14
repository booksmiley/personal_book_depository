"""ISBN -> book metadata, fetched once and normalized into a single shape.

This module's whole job is to turn an ISBN into ONE consistent `Book` object,
no matter which public API answered. Everything downstream (register / borrow /
return, the DB) depends only on `Book`, never on the raw API JSON. That decoupling
is the point: if an API changes or you add a third source, only this file changes.
"""

from dataclasses import asdict, dataclass

import requests

OPEN_LIBRARY_URL = "https://openlibrary.org/isbn/{isbn}.json"
GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
COVER_URL = "https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg"

TIMEOUT = 6  # seconds — never let a slow API hang the request


@dataclass
class Book:
    """The normalized shape the rest of the app stores in the `books` table."""

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
    """Try Open Library first, fall back to Google Books. Return a Book or None.

    This orchestration is done for you — note the pattern: each source is a
    function returning `Book | None`, and we take the first that succeeds. A
    network error from one source shouldn't kill the whole lookup, so we swallow
    `RequestException` and move on.
    """
    for source in (_from_open_library, _from_google_books):
        try:
            book = source(isbn)
        except requests.RequestException:
            book = None
        if book is not None:
            return book
    return None


def _from_open_library(isbn: str) -> Book | None:
    """TODO (your exercise): fetch + parse Open Library into a Book.

    Steps:
      1. resp = requests.get(OPEN_LIBRARY_URL.format(isbn=isbn), timeout=TIMEOUT)
      2. if resp.status_code == 404: return None
      3. data = resp.json()
      4. build and return Book(isbn=isbn, title=data["title"], source="openlibrary", ...)
         - cover_url:  COVER_URL.format(isbn=isbn)
         - publisher:  data.get("publishers", [""])[0]
         - year:       data.get("publish_date", "")
         - GOTCHA — author: data["authors"] is a list of REFERENCES like
           [{"key": "/authors/OL12345A"}], not names. To get the name you must do a
           SECOND request to  https://openlibrary.org{key}.json  and read its "name".
           (Tip: skip this on your first pass — leave author="" and get the round trip
           working, then come back and resolve the author.)
    """
    raise NotImplementedError("write _from_open_library")


def _from_google_books(isbn: str) -> Book | None:
    """TODO (your exercise): fetch + parse Google Books into a Book.

    Steps:
      1. resp = requests.get(GOOGLE_BOOKS_URL.format(isbn=isbn), timeout=TIMEOUT)
      2. data = resp.json()
      3. items = data.get("items"); if not items: return None
      4. info = items[0]["volumeInfo"]; build Book(..., source="googlebooks")
         - title:      info.get("title", "")
         - author:     ", ".join(info.get("authors", []))   # already plain names here
         - publisher:  info.get("publisher", "")
         - year:       info.get("publishedDate", "")
         - cover_url:  info.get("imageLinks", {}).get("thumbnail", "")
    """
    raise NotImplementedError("write _from_google_books")


if __name__ == "__main__":
    # Quick manual check once you've implemented a parser:
    #   python -m book_depository.metadata
    print(fetch_book_metadata("9780131103627"))
