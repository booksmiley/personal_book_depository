"""ISBN metadata via an external dp2 OPAC (ECTS). Optional source."""

import logging
import os
import re

from bs4 import BeautifulSoup

from book_depository import throttle
from book_depository.isbn import is_chinese_or_us_isbn

log = logging.getLogger(__name__)

_HOST = "58.87.101.80"
_BASE = "http://58.87.101.80/ectssh/"
_SEARCH = _BASE + "searchbiblio.aspx"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
_TIMEOUT = 20
_MIN_INTERVAL = float(os.environ.get("ECTS_MIN_INTERVAL", "3.0"))


def fetch_ects_metadata(isbn: str) -> dict | None:
    """Return {title, author, publisher, year}, or None."""
    import requests

    if not is_chinese_or_us_isbn(isbn):
        return None

    throttle.wait(_HOST, min_interval=_MIN_INTERVAL)
    try:
        with requests.Session() as session:
            session.headers.update(_HEADERS)
            page = session.get(_SEARCH, timeout=_TIMEOUT)
            form = _search_form(page.text, isbn)
            if form is None:
                return None
            result = session.post(_SEARCH, data=form, timeout=_TIMEOUT)
            link = _first_book_link(result.text)
            if not link:
                return None
            throttle.wait(_HOST, min_interval=_MIN_INTERVAL)
            detail = session.get(_BASE + link, timeout=_TIMEOUT)
    except requests.RequestException as err:
        log.warning("ects request failed for %s: %s", isbn, err)
        return None

    try:
        return _parse_detail(detail.text)
    except Exception as err:
        log.warning("ects parse failed for %s: %s", isbn, err)
        return None


def _search_form(html: str, isbn: str) -> dict | None:
    form = {}
    for tag in re.findall(r"<input[^>]*>", html):
        name = re.search(r'name="([^"]+)"', tag)
        if not name:
            continue
        value = re.search(r'value="([^"]*)"', tag)
        form[name.group(1)] = value.group(1) if value else ""
    if "__VIEWSTATE" not in form:
        return None
    form.update(
        {
            "BiblioSearchControl1$from0": "isbn",
            "BiblioSearchControl1$match0": "exact",
            "BiblioSearchControl1$db0": "<全部>",
            "BiblioSearchControl1$word0": isbn,
        }
    )
    form.setdefault("BiblioSearchControl1$search_button", "检索")
    return form


def _first_book_link(html: str) -> str:
    m = re.search(r'book\.aspx\?BiblioRecPath=[^"\'&\s]+', html)
    return m.group(0) if m else ""


def _parse_detail(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    fields: dict[str, str] = {}
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) >= 2:
            key = tds[0].get_text(strip=True)
            val = tds[1].get_text(" ", strip=True)
            if key and val:
                fields.setdefault(key, val)

    title, author = _split_title_resp(fields.get("题名与责任说明", ""))
    if not title:
        return None
    if not author:
        author = fields.get("责任者附加项", "")
    publisher, year = _parse_pub(fields.get("出版发行", ""))
    return {"title": title, "author": author, "publisher": publisher, "year": year}


def _split_title_resp(s: str) -> tuple[str, str]:
    if not s:
        return "", ""
    # MARC 245 separates title from responsibility with " /" (space-slash), so split on
    # that — not any "/" — to keep title-internal slashes (e.g. "支持/反驳加尔文主义").
    parts = re.split(r"\s+/", s, maxsplit=1)
    title = parts[0].strip().rstrip(" .：:")
    author = ""
    if len(parts) > 1:
        resp = parts[1].strip()
        m = re.match(r"^(.*?)\s*(?:主编|编著|编选|编|著|撰|主译|合译|译)", resp)
        author = (m.group(1) if m else resp).strip().rstrip(" .，,；;")
    return title, author


def _parse_pub(s: str) -> tuple[str, str]:
    publisher = ""
    m = re.search(r"[:：]\s*([^,，]+)", s)
    if m:
        publisher = m.group(1).strip()
    year_m = re.search(r"\b(\d{4})\b", s)
    return publisher, (year_m.group(1) if year_m else "")
