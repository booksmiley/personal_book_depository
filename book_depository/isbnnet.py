"""Scraper for Taiwan's ISBN registry — 全國新書資訊網 (isbn.ncl.edu.tw).

Run by Taiwan's National Central Library, this is the authoritative source for any
book with a Taiwan ISBN. It carries accurate Traditional Chinese metadata for
Taiwan-published books — including the many Christian titles (校園書房, 道聲, 橄欖…)
that mainland sources miss or render in Simplified Chinese. (For the church's
"21世紀舊約導論", Google returned a wrong Simplified title; ISBNnet has it right.)

No official API. The simple search is a POST that first needs a CSRF token and a
session cookie from the index page:
  1. GET index.php             -> sets the session cookie, embeds a csrftoken
  2. POST H30_SearchBooks.php  -> FO_SearchField0=ISBN + the value + the token
  3. parse the first row of <table class="table-searchbooks">

Two quirks handled below:
  - Dates are ROC (Minguo) calendar: "88/09" -> 1999-09 (ROC year + 1911).
  - Only Taiwan ISBNs are looked up (registration groups 957/986/626/627 after the
    978 prefix); everything else returns None instantly with no network call, so
    this source is free to put first in the fallback chain.
"""

import logging
import re

from bs4 import BeautifulSoup

from book_depository import throttle

log = logging.getLogger(__name__)

_HOST = "isbn.ncl.edu.tw"
_BASE = "https://isbn.ncl.edu.tw/NEW_ISBNNet/"
_INDEX = _BASE + "index.php"
_SEARCH = _BASE + "H30_SearchBooks.php?Pact=DisplayAll4Simple"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
_TIMEOUT = 12
_ROC_OFFSET = 1911

# The 3 digits after the 978 prefix that identify a Taiwan-registered ISBN.
TAIWAN_GROUPS = {"957", "986", "626", "627"}


def is_taiwan_isbn(isbn: str) -> bool:
    return len(isbn) == 13 and isbn.startswith("978") and isbn[3:6] in TAIWAN_GROUPS


async def fetch_isbnnet_metadata(isbn: str) -> dict | None:
    """Return {title, author, publisher, year} for a Taiwan ISBN, else None."""
    import httpx  # local import keeps startup fast when unused

    if not is_taiwan_isbn(isbn):
        return None  # not a Taiwan ISBN — skip without touching the network

    await throttle.wait_async(_HOST)  # pace hits to the NCL registry
    try:
        # One AsyncClient keeps the session cookie across the CSRF GET and the POST.
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True
        ) as client:
            index = await client.get(_INDEX)
            token = _csrf_token(index.text)
            if not token:
                log.debug("isbnnet: no csrf token for %s", isbn)
                return None
            resp = await client.post(_SEARCH, data=_search_form(isbn, token))
    except httpx.HTTPError as err:
        log.warning("isbnnet request failed for %s: %s", isbn, err)
        return None

    if resp.status_code != 200:
        log.debug("isbnnet HTTP %s for %s", resp.status_code, isbn)
        return None

    try:
        return _parse(resp.text)
    except Exception as err:  # never let a parse hiccup sink the fallback chain
        log.warning("isbnnet parse failed for %s: %s", isbn, err)
        return None


def _search_form(isbn: str, token: str) -> dict:
    # Field names mirror the site's simple-search form (some keys are Chinese).
    return {
        "FO_SchRe1ation0": "Null",
        "FO_SearchField0": "ISBN",
        "FO_SearchValue0": isbn,
        "FB_clicked": "FB_開始查詢",
        "FB_pageSID": "Simple",
        "FO_Match": "2",
        "FO_每頁筆數": "10",
        "FO_目前頁數": "1",
        "FO_資料排序": "PubMonth_Pre DESC",
        "FB_ListOri": "",
        "csrftoken": token,
    }


def _csrf_token(html: str) -> str:
    m = re.search(r"csrftoken[^>]*?value=['\"]?([^'\">]+)", html)
    return m.group(1) if m else ""


def _parse(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="table-searchbooks")
    if not table:
        return None

    # Each result cell carries a data-th label (書名/作者/出版者/日期). The first row
    # that has a 書名 cell is the top hit.
    for tr in table.find_all("tr"):
        cells = {
            td.get("data-th"): td.get_text(" ", strip=True)
            for td in tr.find_all("td")
            if td.get("data-th")
        }
        title = (cells.get("書名") or "").strip()
        if title:
            return {
                "title": title,
                "author": (cells.get("作者") or "").strip(),
                "publisher": (cells.get("出版者") or "").strip(),
                "year": _roc_year(cells.get("日期") or ""),
            }
    return None


def _roc_year(raw: str) -> str:
    """'88/09' -> '1999' (ROC year + 1911). A 4-digit Gregorian year passes through."""
    m = re.match(r"\s*(\d+)", raw)
    if not m:
        return ""
    n = int(m.group(1))
    return str(n + _ROC_OFFSET) if n < _ROC_OFFSET else str(n)
