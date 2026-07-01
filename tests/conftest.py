"""Shared fixtures. Tests never touch the network or the real data dir."""

import pytest

from book_depository import db
from book_depository.metadata import Book


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point db at a throwaway data dir for the test (each test gets a fresh one)."""
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def conn(data_dir):
    """A fresh SQLite connection (migrations applied) for owner 'test'."""
    connection = db.get_db("test")
    yield connection
    connection.close()


@pytest.fixture
def book():
    """Factory for a minimal Book: book() or book(isbn=..., title=...)."""
    def _make(isbn="9780000000002", title="Test Title", **kw):
        fields = dict(author="An Author", publisher="A Publisher", year="2020", source="TEST")
        fields.update(kw)
        return Book(isbn=isbn, title=title, **fields)

    return _make
