"""Scraper for the China National Library OPAC (https://opac.nlc.cn).

No official API exists. This replicates the ISBN-search flow used by the
NLCISBNPlugin for Calibre (github.com/DoiiarX/NLCISBNPlugin):
  1. Load the base URL to establish a session cookie.
  2. Load the ISBN search URL with the same session.
  3. Parse metadata out of the two-column HTML table (<table id="td">).

The NLC OPAC is served over HTTP/3 (QUIC), which Python's `requests`/`urllib`
don't support. We drive a headless Chromium via Playwright instead, which
handles QUIC exactly as Chrome does.

Playwright is a local-only dependency (requirements-local.txt). On Render or
any environment where it isn't installed, this module returns None silently
and the fallback chain in metadata.py just skips it.

First-time setup:
    pip install -r requirements-local.txt
    playwright install chromium
"""

import logging
import re

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

try:
    from playwright.sync_api import sync_playwright

    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

BASE_URL = "https://opac.nlc.cn/F"
SEARCH_URL = (
    BASE_URL
    + "?func=find-b&find_code=ISB&request={isbn}&local_base=NLC01"
    + "&filter_code_1=WLN&filter_request_1=&filter_code_2=WYR&filter_request_2="
    + "&filter_code_3=WYR&filter_request_3=&filter_code_4=WFM&filter_request_4="
    + "&filter_code_5=WSL&filter_request_5="
)


def fetch_nlc_metadata(isbn: str) -> dict | None:
    """Fetch metadata from the NLC OPAC by ISBN.

    Returns a dict with keys: title, author, publisher, year.
    Returns None if Playwright isn't installed, the book isn't found,
    or anything goes wrong. All exceptions are caught here.
    """
    if not _PLAYWRIGHT_AVAILABLE:
        log.debug("playwright not installed; NLC lookup skipped")
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            # Session handshake — visit base URL first so the OPAC sets its
            # session cookie before we hit the search endpoint.
            page.goto(BASE_URL, timeout=15000)
            page.goto(SEARCH_URL.format(isbn=isbn), timeout=15000)
            html = page.content()
            browser.close()
        return _parse(html, isbn)
    except Exception as err:
        log.warning("NLC scrape failed for %s: %s", isbn, err)
        return None


def _parse(html: str, isbn: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", attrs={"id": "td"})
    if not table:
        return None

    # Build a key→value map from the two-column table rows.
    data: dict[str, str] = {}
    prev_key = ""
    prev_val = ""
    for tr in table.find_all("tr"):
        tds = tr.find_all("td", class_="td1")
        if len(tds) != 2:
            continue
        key = tds[0].get_text(strip=True).replace("\xa0", " ")
        val = tds[1].get_text(strip=True).replace("\xa0", " ")
        if not key and not val:
            continue
        if key:
            data[key] = val
            prev_key, prev_val = key, val
        else:
            # Continuation row — append to the previous key.
            data[prev_key] = "\n".join([prev_val, val]).strip()
            prev_val = data[prev_key]

    if not data:
        return None

    title = _parse_title(data.get("题名与责任", ""))
    if not title:
        return None

    author = _parse_author(data.get("著者", ""))
    publisher, year = _parse_pub(data.get("出版项", ""))

    # 通用数据 positions 9-13 hold the year when 出版项 doesn't have one.
    if not year:
        m = re.search(r"\d{9}(\d{4})", data.get("通用数据", ""))
        if m:
            year = m.group(1)

    return {"title": title, "author": author, "publisher": publisher, "year": year}


def _parse_title(raw: str) -> str:
    """Strip language-type suffix like ' [英文]' from the title."""
    if not raw:
        return ""
    m = re.search(
        r"([一-龥a-zA-Z0-9][一-龥a-zA-Z0-9\s\-:：／/]*?)" r"(?=\s*\[[一-龥]{2}\]|$)",
        raw,
    )
    return m.group(1).strip() if m else raw.strip()


def _parse_author(raw: str) -> str:
    """Take the first author and strip the trailing role word (著/编/译/…)."""
    if not raw:
        return ""
    first = raw.split("\n")[0].strip()
    m = re.match(r"^(.*?)\s+(?:著|编|译|撰|主编|整理)", first)
    return m.group(1).strip() if m else first


def _parse_pub(raw: str) -> tuple[str, str]:
    """Parse '出版项' like '北京 : 机械工业出版社, 2018' → (publisher, year)."""
    publisher = ""
    year = ""
    pub_m = re.search(r":\s*(.+?),", raw)
    if pub_m:
        publisher = pub_m.group(1).strip()
    year_m = re.search(r"\b(\d{4})\b", raw)
    if year_m:
        year = year_m.group(1)
    return publisher, year
