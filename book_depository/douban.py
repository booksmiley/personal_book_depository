"""Scraper for Douban Book (book.douban.com) ISBN pages.

Douban is China's largest book/film/music community and has excellent metadata
for Chinese-language books — proper Chinese characters for title, author, and
publisher, plus cover images.

There is no official API. We scrape the public ISBN redirect:
    https://book.douban.com/isbn/{isbn}
which redirects to the subject page and returns HTML.

A browser-like User-Agent is required; without it Douban returns 418.
"""

import logging
import re

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_URL = "https://book.douban.com/isbn/{isbn}"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
_TIMEOUT = 10


def fetch_douban_metadata(isbn: str) -> dict | None:
    """Return a dict with keys: title, author, publisher, year, cover_url.

    Returns None if the book isn't found or anything goes wrong.
    """
    import requests  # local import keeps startup fast when unused

    try:
        resp = requests.get(
            _URL.format(isbn=isbn),
            headers=_HEADERS,
            timeout=_TIMEOUT,
            allow_redirects=True,
        )
    except requests.RequestException as err:
        log.warning("douban request failed for %s: %s", isbn, err)
        return None

    if resp.status_code != 200:
        log.debug("douban HTTP %s for %s", resp.status_code, isbn)
        return None

    return _parse(resp.text, isbn)


def _parse(html: str, isbn: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    if not h1:
        return None
    title = h1.get_text(strip=True)
    if not title:
        return None

    info = soup.find(id="info")
    fields = _parse_info(info) if info else {}

    cover_url = ""
    nbg = soup.find("a", class_="nbg")
    if nbg:
        cover_url = nbg.get("href", "")

    return {
        "title": title,
        "author": fields.get("作者", ""),
        "publisher": fields.get("出版社", ""),
        "year": _clean_year(fields.get("出版年", "")),
        "cover_url": cover_url,
    }


def _parse_info(info_div) -> dict[str, str]:
    """Extract label→value pairs from Douban's #info div.

    Two layouts exist in the wild:
      - Author/translator rows: <span><span class="pl">作者</span>: <a>…</a></span>
      - Other fields:           <span class="pl">出版社:</span> text <br>

    In both cases, pl.next_siblings gives the value nodes — the outer <span>
    boundary naturally limits author rows, and we stop at <br> for other fields.
    """
    result: dict[str, str] = {}

    for pl in info_div.find_all("span", class_="pl"):
        label = pl.get_text(strip=True).rstrip(":：").strip()
        if not label:
            continue

        parts = []
        for node in pl.next_siblings:
            tag = getattr(node, "name", None)
            if tag == "br":
                break
            if tag == "span" and "pl" in (node.get("class") or []):
                break
            text = (
                node.get_text(strip=True)
                if hasattr(node, "get_text")
                else str(node).strip()
            )
            if text and text not in (":", "：", "/"):
                parts.append(text)

        if parts:
            result[label] = " / ".join(parts)

    return result


def _clean_year(raw: str) -> str:
    m = re.search(r"\b(\d{4})\b", raw)
    return m.group(1) if m else raw
