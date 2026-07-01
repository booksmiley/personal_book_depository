"""Open Library: ISBN lookup, author resolution, and title search.

Returns plain dicts (or None / lists); `book_depository.metadata` wraps them into
`Book`. Never imports the aggregator, so there's no circular dependency.
"""

import logging

import requests

from book_depository import throttle

log = logging.getLogger(__name__)

_HOST = "openlibrary.org"
_ISBN_URL = "https://openlibrary.org/isbn/{isbn}.json"
_SEARCH_URL = "https://openlibrary.org/search.json"
_AUTHORS_URL = "https://openlibrary.org{key}.json"
_COVER_URL = "https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg"
TIMEOUT = 10


def cover_url(isbn: str) -> str:
    """Static Open Library cover URL (no network; may resolve to a blank image)."""
    return _COVER_URL.format(isbn=isbn)


def resolve_authors(authors_refs: list) -> str:
    authors = []
    for ref in authors_refs:
        ref_key = ref["key"]
        throttle.wait(_HOST)
        resp = requests.get(_AUTHORS_URL.format(key=ref_key), timeout=TIMEOUT)
        log.debug("author lookup %s -> HTTP %s", ref_key, resp.status_code)
        authors.append(resp.json().get("name", ""))
    return ", ".join(authors)


def fetch_open_library_metadata(isbn: str) -> dict | None:
    throttle.wait(_HOST)
    resp = requests.get(_ISBN_URL.format(isbn=isbn), timeout=TIMEOUT)
    log.debug("open library %s -> HTTP %s", isbn, resp.status_code)
    if resp.status_code == 404:
        return None
    data = resp.json()
    # Author resolution is an extra network call (one per author). It's a nice-to-have,
    # so a failure here must NOT sink the whole book — degrade to an empty author.
    try:
        authors = resolve_authors(data.get("authors", []))
    except requests.RequestException as err:
        log.warning("author resolution failed for %s: %s", isbn, err)
        authors = ""
    return {
        "title": data.get("title", ""),
        "author": authors,
        "cover_url": cover_url(isbn),
        "publisher": data.get("publishers", [""])[0],  # OL: list, not a string
        "year": data.get("publish_date", ""),  # OL: publish_date, not publishedDate
    }


def search_open_library(query: str, limit: int) -> list[dict]:
    params = {
        "q": query,  # general query (matches title/author), forgiving
        "limit": limit,
        "fields": "title,author_name,first_publish_year,isbn,publisher,cover_i",
    }
    throttle.wait(_HOST)
    resp = requests.get(_SEARCH_URL, params=params, timeout=TIMEOUT)
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
            {
                "isbn": isbn13,
                "title": doc.get("title", ""),
                "author": ", ".join((doc.get("author_name") or [])[:3]),
                "cover_url": cover,
                "publisher": (doc.get("publisher") or [""])[0],
                "year": str(doc.get("first_publish_year") or ""),
            }
        )
    return out
