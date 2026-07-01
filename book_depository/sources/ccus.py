"""ISBN metadata via an external Chinese-Christian bookstore (server-rendered,
ISBN-keyed). Fills a gap for US-published Chinese titles the mainstream APIs miss.
Optional source."""

import logging
import os
import re

from bs4 import BeautifulSoup

from book_depository import throttle
from book_depository.isbn import is_chinese_or_us_isbn

log = logging.getLogger(__name__)

_HOST = "www.cc-us.org"
_URL = "https://www.cc-us.org/shop/c{isbn}/"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
_TIMEOUT = 20
_MIN_INTERVAL = float(os.environ.get("CCUS_MIN_INTERVAL", "3.0"))


def fetch_ccus_metadata(isbn: str) -> dict | None:
    import requests

    if not is_chinese_or_us_isbn(isbn):
        return None

    throttle.wait(_HOST, min_interval=_MIN_INTERVAL)
    try:
        resp = requests.get(_URL.format(isbn=isbn), headers=_HEADERS, timeout=_TIMEOUT)
    except requests.RequestException as err:
        log.warning("ccus request failed for %s: %s", isbn, err)
        return None
    if resp.status_code == 404:
        return None  # not carried

    try:
        return _parse(resp.text)
    except Exception as err:
        log.warning("ccus parse failed for %s: %s", isbn, err)
        return None


def _parse(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else ""
    if not title:
        return None
    text = soup.get_text("\n", strip=True)
    return {
        "title": title,
        "author": _field(text, "作者"),
        "publisher": _field(text, "出版社"),
        "year": _year(_field(text, "出版日期")),
    }


def _field(text: str, label: str) -> str:
    m = re.search(rf"{label}[：:]\s*([^\n]+)", text)
    return m.group(1).strip() if m else ""


def _year(s: str) -> str:
    m = re.search(r"\d{4}", s)
    return m.group(0) if m else ""
