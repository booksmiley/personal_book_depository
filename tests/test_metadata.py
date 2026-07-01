"""Unit tests for the metadata aggregation layer (sources are mocked — no network)."""

from book_depository import metadata
from book_depository.metadata import (
    ApiSource,
    Book,
    _has_core,
    _merge_into,
    fetch_book_metadata,
    search_by_title,
)


def test_merge_into_fills_only_empty_fields():
    base = Book(isbn="x", title="Keep", author="")
    extra = Book(isbn="x", title="Ignore", author="Added", publisher="P")
    assert _merge_into(base, extra) is True
    assert base.author == "Added"      # empty field filled
    assert base.publisher == "P"
    assert base.title == "Keep"        # existing field untouched


def test_merge_into_reports_no_contribution():
    base = Book(isbn="x", title="T", author="A", publisher="P", year="2020",
                cover_url="c", language="en")
    assert _merge_into(base, Book(isbn="x", title="Z")) is False


def test_has_core():
    assert _has_core(Book(isbn="x", title="T", author="A", publisher="P", year="2020"))
    assert not _has_core(Book(isbn="x", title="T", author="A"))  # missing publisher/year


def test_fetch_combines_sources_and_stops_at_core(monkeypatch):
    calls = []

    def isbnnet(isbn):
        calls.append("isbnnet")
        return Book(isbn=isbn, title="T", author="A", source="ISBNNET")

    def google(isbn):
        calls.append("google")
        return Book(isbn=isbn, title="", publisher="P", year="2020", source="GOOGLE")

    def none(name):
        def fn(isbn):
            calls.append(name)
            return None
        return fn

    monkeypatch.setattr(metadata, "_from_isbnnet", isbnnet)
    monkeypatch.setattr(metadata, "_from_douban", none("douban"))
    monkeypatch.setattr(metadata, "_from_open_library", none("openlib"))
    monkeypatch.setattr(metadata, "_from_google_books", google)
    monkeypatch.setattr(metadata, "_from_ccus", none("ccus"))
    monkeypatch.setattr(metadata, "_from_ects", none("ects"))

    book = fetch_book_metadata("9780000000001")
    assert (book.title, book.author, book.publisher, book.year) == ("T", "A", "P", "2020")
    assert book.source == "ISBNNET+GOOGLE"       # only the contributors, in order
    assert "ccus" not in calls and "ects" not in calls  # stopped once core complete


def test_fetch_returns_none_when_all_sources_miss(monkeypatch):
    for name in ("_from_isbnnet", "_from_douban", "_from_open_library",
                 "_from_google_books", "_from_ccus", "_from_ects"):
        monkeypatch.setattr(metadata, name, lambda isbn: None)
    assert fetch_book_metadata("9780000000001") is None


def test_search_by_title_dedups_and_labels(monkeypatch):
    google_hits = [
        {"isbn": "9780000000001", "title": "A"},
        {"isbn": "9780000000002", "title": "B"},
    ]
    openlib_hits = [
        {"isbn": "9780000000002", "title": "B duplicate"},  # dropped (dup ISBN)
        {"isbn": "9780000000003", "title": "C"},
        {"isbn": "", "title": "no isbn"},                   # dropped (no ISBN)
    ]
    monkeypatch.setattr(metadata, "_TITLE_SEARCHERS", (
        (lambda q, limit: google_hits, ApiSource.google),
        (lambda q, limit: openlib_hits, ApiSource.open_lib),
    ))
    results = search_by_title("anything")
    assert [b.isbn for b in results] == [
        "9780000000001", "9780000000002", "9780000000003",
    ]
    assert results[0].source == "GOOGLE"
    assert results[2].source == "OPEN_LIB"


def test_search_by_title_empty_query_returns_nothing():
    assert search_by_title("   ") == []
