"""Integration tests for the DB layer against a real (throwaway) SQLite file."""

import threading

from book_depository import db

ISBN = "9780000000002"


def test_add_and_find_book(conn, book):
    book_id = db.add_book(conn, book())
    assert book_id
    row = db.find_book_by_isbn(conn, ISBN)
    assert row["title"] == "Test Title"
    assert row["total_count"] == 1 and row["available"] == 1


def test_add_copy_bumps_counts(conn, book):
    db.add_book(conn, book())
    db.add_copy(conn, ISBN)
    row = db.find_book_by_isbn(conn, ISBN)
    assert row["total_count"] == 2 and row["available"] == 2


def test_borrow_then_return(conn, book):
    book_id = db.add_book(conn, book())
    loan_id = db.borrow_book(conn, book_id, "Alice")
    assert loan_id
    assert db.find_book_by_isbn(conn, ISBN)["available"] == 0
    assert db.borrow_book(conn, book_id, "Bob") is None  # none left to borrow
    assert db.close_loan(conn, loan_id) == 1             # returned -> 1 available
    assert db.close_loan(conn, loan_id) is None          # can't return twice


def test_update_book_recomputes_available(conn, book):
    book_id = db.add_book(conn, book())
    db.add_copy(conn, ISBN)                 # total 2, available 2
    db.borrow_book(conn, book_id, "Alice")  # available 1
    db.update_book(conn, ISBN, {"total_count": 5})
    row = db.find_book_by_isbn(conn, ISBN)
    assert row["total_count"] == 5 and row["available"] == 4  # 5 total - 1 open loan


def test_delete_book(conn, book):
    db.add_book(conn, book())
    assert db.delete_book(conn, ISBN) is True
    assert db.find_book_by_isbn(conn, ISBN) is None
    assert db.delete_book(conn, ISBN) is False  # already gone


def test_concurrent_borrow_never_overborrows(conn, book):
    book_id = db.add_book(conn, book())
    for _ in range(4):
        db.add_copy(conn, ISBN)  # total 5, available 5

    results = []
    lock = threading.Lock()

    def worker():
        c = db.get_db("test")  # each thread needs its own connection
        try:
            loan = db.borrow_book(c, book_id, "racer")
        finally:
            c.close()
        with lock:
            results.append(loan)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    succeeded = [r for r in results if r is not None]
    assert len(succeeded) == 5  # exactly the 5 copies, no more
    assert db.find_book_by_isbn(conn, ISBN)["available"] == 0


def test_new_book_records_added_at(conn, book):
    db.add_book(conn, book())
    row = db.find_book_by_isbn(conn, ISBN)
    assert row["added_at"]  # set by BOOK_INSERT's datetime('now')
    assert conn.execute("PRAGMA user_version").fetchone()[0] >= 3


def test_added_at_migration_backfills_existing_rows(data_dir):
    import sqlite3

    from db_lib import run_migrations

    # A legacy DB at user_version 2 (pre-added_at) with a row already in it.
    path = data_dir / "legacy.sqlite"
    raw = sqlite3.connect(path)
    raw.executescript(
        "CREATE TABLE books (book_id INTEGER PRIMARY KEY, isbn TEXT UNIQUE, title TEXT,"
        " author TEXT, cover_url TEXT, publisher TEXT, year TEXT, source TEXT,"
        " language TEXT, total_count INTEGER DEFAULT 1, available INTEGER DEFAULT 1);"
        " INSERT INTO books (isbn, title) VALUES ('9780000000009', 'Legacy');"
        " PRAGMA user_version = 2;"
    )
    raw.commit()

    run_migrations(raw)  # applies 0003 only

    assert raw.execute("PRAGMA user_version").fetchone()[0] == 3
    added = raw.execute(
        "SELECT added_at FROM books WHERE isbn = '9780000000009'"
    ).fetchone()[0]
    assert added  # existing row backfilled with the migration time
    raw.close()


def test_migrations_run_and_are_idempotent(data_dir):
    c1 = db.get_db("mtest")
    v1 = c1.execute("PRAGMA user_version").fetchone()[0]
    c1.close()
    c2 = db.get_db("mtest")  # second open must not re-run or bump the version
    v2 = c2.execute("PRAGMA user_version").fetchone()[0]
    c2.close()
    assert v1 > 0 and v1 == v2
