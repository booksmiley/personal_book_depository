"""Persistence — SQLite, one file per owner ("DB-per-owner" from the brief).

Nothing in the scan->lookup slice touches this module. It comes into play in the
*register* flow, when the user confirms a scanned book should be added.

Why SQLite + stdlib `sqlite3`: no server, one file per owner sidesteps multi-tenancy
entirely, and it's all own-able code — ideal for practice. On a real deploy the file
needs to sit on a PERSISTENT disk/volume or it gets wiped on redeploy.
"""

import os
import sqlite3
from pathlib import Path

from db_lib import run_migrations

# Where the per-owner SQLite files live. Defaults to ./data inside the repo (git
# -ignored), but BOOK_DATA_DIR overrides it so local runs can keep personal library
# data in a safe location outside the project. run_local.py sets this from config.yml.
DATA_DIR = Path(
    os.environ.get("BOOK_DATA_DIR") or Path(__file__).resolve().parent.parent / "data"
)
BACKUP_DIR = os.environ.get("BOOK_BACKUP_DIR", "")

# The schema is now defined by the numbered .sql files in db_lib/, applied in order
# and tracked per-DB via PRAGMA user_version (see db_lib/__init__.py). New columns =
# drop a new NNNN_*.sql file; it runs automatically on the next connection.


BOOK_QUERY_BY_ISBN = "SELECT * FROM books WHERE isbn = ?"
BOOK_INSERT = "INSERT INTO books (isbn, title, author, cover_url, publisher, year, source, language) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
BOOK_UPDATE_COPY = (
    "UPDATE books SET total_count = total_count + 1, available = available + 1 "
    "WHERE isbn = ?"
)

QUERY_AVAILABLE_COPIES = """
    SELECT available FROM books WHERE book_id = ?
"""


def get_db(owner: str) -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DATA_DIR / f"{owner}.sqlite")
    conn.row_factory = sqlite3.Row  # rows behave like dicts
    conn.execute("PRAGMA foreign_keys = ON")  # sqlite needs this per-connection
    # WAL is required by Litestream (it ships the write-ahead log to object storage);
    # journal_mode is persisted in the DB header, so this is a no-op after the first
    # time. busy_timeout lets a write wait briefly instead of failing instantly if
    # Litestream is mid-checkpoint.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    run_migrations(conn)  # apply any pending schema migrations (idempotent)
    return conn


def find_book_by_isbn(conn: sqlite3.Connection, isbn: str):
    cur = conn.execute(BOOK_QUERY_BY_ISBN, (isbn,))
    row = cur.fetchone()
    return row


def add_book(conn: sqlite3.Connection, book) -> int:
    cur = conn.execute(
        BOOK_INSERT,
        (
            book.isbn,
            book.title,
            book.author,
            book.cover_url,
            book.publisher,
            book.year,
            book.source,
            book.language,
        ),
    )
    conn.commit()
    return cur.lastrowid


def add_copy(conn: sqlite3.Connection, isbn: str) -> int:
    cur = conn.execute(BOOK_UPDATE_COPY, (isbn,))
    conn.commit()
    return cur.rowcount


# Fields an admin may edit. NOT isbn (the key) and NOT available (derived from
# total_count minus open loans). These names are a fixed whitelist — never user
# input — so it's safe to interpolate them into the UPDATE column list below.
EDITABLE_FIELDS = ("title", "author", "publisher", "year", "language", "cover_url", "total_count")


def update_book(conn: sqlite3.Connection, isbn: str, fields: dict):
    """Update whitelisted fields of a book. If total_count changes, recompute
    available = total_count - (open loans). Returns the fresh row, or None if the
    book doesn't exist."""
    book = find_book_by_isbn(conn, isbn)
    if book is None:
        return None

    updates = {k: v for k, v in fields.items() if k in EDITABLE_FIELDS}
    if not updates:
        return book

    if "total_count" in updates:
        try:
            total = max(0, int(updates["total_count"]))
        except (TypeError, ValueError):
            total = book["total_count"]
        updates["total_count"] = total
        out = len(open_loans(conn, book["book_id"]))
        updates["available"] = max(0, total - out)  # keep availability consistent

    columns = ", ".join(f"{k} = ?" for k in updates)  # keys are the whitelist above
    conn.execute(f"UPDATE books SET {columns} WHERE isbn = ?", (*updates.values(), isbn))
    conn.commit()
    return find_book_by_isbn(conn, isbn)


def delete_book(conn: sqlite3.Connection, isbn: str) -> bool:
    """Delete a book and its loan history. Returns False if it wasn't found."""
    book = find_book_by_isbn(conn, isbn)
    if book is None:
        return False
    conn.execute("DELETE FROM loans WHERE book_id = ?", (book["book_id"],))
    conn.execute("DELETE FROM books WHERE isbn = ?", (isbn,))
    conn.commit()
    return True


def open_loans(conn: sqlite3.Connection, book_id: int):
    query_open_loan = """
        SELECT * from loans
        WHERE book_id = ? AND returned_at is NULL
        ORDER BY borrowed_at
    """
    cur = conn.execute(query_open_loan, (book_id,))
    return cur.fetchall()


def borrow_book(conn: sqlite3.Connection, book_id: int, borrower: str):
    """Atomically take one copy and open a loan. Returns the new loan_id, or None
    if no copies were available (the UPDATE matched no row)."""
    query_borrow_one_copy_from_books = """
        UPDATE books SET available = available - 1
        WHERE book_id = ? AND available > 0
    """
    query_add_record_to_loan = """
        INSERT INTO loans (book_id, borrower) VALUES (?, ?)
    """

    cur = conn.execute(query_borrow_one_copy_from_books, (book_id,))
    if cur.rowcount == 0:
        return None

    cur = conn.execute(query_add_record_to_loan, (book_id, borrower))
    loan_id = cur.lastrowid
    conn.commit()

    return loan_id


def close_loan(conn: sqlite3.Connection, loan_id: int):
    query_set_return_time_in_loan = """
        UPDATE loans SET returned_at = datetime('now')
        WHERE loan_id = ? AND returned_at IS NULL
    """

    query_book_id_from_loan_id = """
        SELECT book_id FROM loans WHERE loan_id = ?
    """

    query_return_copy_in_books = """
        UPDATE books SET available = available + 1 
        WHERE book_id = ?
    """

    cur = conn.execute(query_set_return_time_in_loan, (loan_id,))
    if cur.rowcount == 0:
        return None

    book_id = conn.execute(query_book_id_from_loan_id, (loan_id,)).fetchone()["book_id"]
    conn.execute(query_return_copy_in_books, (book_id,))

    available = conn.execute(QUERY_AVAILABLE_COPIES, (book_id,)).fetchone()["available"]
    conn.commit()
    return available


def get_all_books(conn: sqlite3.Connection):
    cur = conn.execute("SELECT * FROM books ORDER BY title")
    return cur.fetchall()


def backup_db(conn: sqlite3.Connection, backup_path: str):
    if not backup_path:
        return

    backup_dir = Path(backup_path).expanduser()
    backup_dir.mkdir(parents=True, exist_ok=True)
    temp_path = backup_dir / "lib_admin_temp.sqlite"
    final_path = backup_dir / "lib_admin.sqlite"
    with sqlite3.connect(temp_path) as back_up_conn:
        conn.backup(back_up_conn)

    os.replace(temp_path, final_path)
