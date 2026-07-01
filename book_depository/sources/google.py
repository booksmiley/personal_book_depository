"""Google Books: ISBN lookup, cover thumbnail, and title search.

Returns plain dicts (or None / lists); `book_depository.metadata` wraps them into
`Book`. Never imports the aggregator, so there's no circular dependency.
"""

import logging
import os

import requests

from book_depository import throttle
from book_depository.isbn import isbn10_to_isbn13, isbn13_to_isbn10

log = logging.getLogger(__name__)

API_KEY = os.environ.get("GOOGLE_BOOKS_API_KEY", "")
# Google Books returns different (sometimes empty) results depending on the caller's
# country; pin it so results are deterministic regardless of where we deploy.
COUNTRY = os.environ.get("GOOGLE_BOOKS_COUNTRY", "US")
_URL = "https://www.googleapis.com/books/v1/volumes"
_HOST = "www.googleapis.com"
TIMEOUT = 10


def _thumb(info: dict) -> str:
    """Cover thumbnail, upgraded to https. Google returns http:// links, which get
    dropped by the frontend's https-only guard (and are mixed content on Render);
    the image serves fine over https."""
    url = info.get("imageLinks", {}).get("thumbnail", "")
    return "https://" + url[len("http://"):] if url.startswith("http://") else url


def _volume(isbn: str) -> dict | None:
    """Return the first matching volumeInfo dict for an ISBN, or None."""
    params = {"q": f"isbn:{isbn}", "country": COUNTRY}
    if API_KEY:
        params["key"] = API_KEY
    throttle.wait(_HOST)
    resp = requests.get(_URL, params=params, timeout=TIMEOUT)
    items = resp.json().get("items")
    if not items:
        return None
    return items[0]["volumeInfo"]


def fetch_google_books_metadata(isbn: str) -> dict | None:
    info = _volume(isbn)
    # Some volumes are indexed only under the ISBN-10. Retry with that form before
    # giving up (free helper, no network unless the first query missed).
    if info is None:
        alt = isbn13_to_isbn10(isbn)
        if alt:
            info = _volume(alt)
    if info is None:
        return None
    return {
        "title": info.get("title", ""),
        "author": ", ".join(info.get("authors", [])),  # Google: plain name strings
        "cover_url": _thumb(info),
        "publisher": info.get("publisher", ""),
        "year": info.get("publishedDate", ""),
        "language": info.get("language", ""),  # ISO code, e.g. "en", "zh"
    }


def thumbnail(isbn: str) -> str:
    """Cover thumbnail URL for an ISBN, or "" if none / unreachable. Used as the
    preferred cover for sources that don't supply a hotlink-friendly one."""
    try:
        info = _volume(isbn)
    except requests.RequestException:
        return ""
    if not info:
        return ""
    return _thumb(info)


def search_google_books(query: str, limit: int) -> list[dict]:
    params = {
        "q": query,  # general query: forgiving if the user types "title author"
        "country": COUNTRY,
        "maxResults": min(limit, 20),
    }
    if API_KEY:
        params["key"] = API_KEY
    throttle.wait(_HOST)
    resp = requests.get(_URL, params=params, timeout=TIMEOUT)
    out = []
    for item in resp.json().get("items", []) or []:
        info = item.get("volumeInfo", {})
        out.append(
            {
                "isbn": _isbn_from_identifiers(info.get("industryIdentifiers", [])),
                "title": info.get("title", ""),
                "author": ", ".join(info.get("authors", [])),
                "cover_url": _thumb(info),
                "publisher": info.get("publisher", ""),
                "year": info.get("publishedDate", ""),
                "language": info.get("language", ""),
            }
        )
    return out


def _isbn_from_identifiers(identifiers: list) -> str:
    ids = {i.get("type"): i.get("identifier", "") for i in identifiers}
    if ids.get("ISBN_13"):
        return ids["ISBN_13"]
    if ids.get("ISBN_10"):
        return isbn10_to_isbn13(ids["ISBN_10"])
    return ""
