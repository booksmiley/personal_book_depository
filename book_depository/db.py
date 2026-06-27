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
BOOK_INSERT = "INSERT INTO books (isbn, title, author, cover_url, publisher, year, source) VALUES (?, ?, ?, ?, ?, ?, ?)"
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
        ),
    )
    conn.commit()
    return cur.lastrowid


def add_copy(conn: sqlite3.Connection, isbn: str) -> int:
    cur = conn.execute(BOOK_UPDATE_COPY, (isbn,))
    conn.commit()
    return cur.rowcount


def open_loans(conn: sqlite3.Connection, book_id: int):
    query_open_loan = """
        SELECT * from loans
        WHERE book_id = ? AND returned_at is NULL
        ORDER BY borrowed_at
    """
    cur = conn.execute(query_open_loan, (book_id,))
    return cur.fetchall()


def borrow_book(conn: sqlite3.Connection, book_id: int, borrower: str):
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

    conn.execute(query_add_record_to_loan, (book_id, borrower))

    cur = conn.execute(QUERY_AVAILABLE_COPIES, (book_id,))
    available = cur.fetchone()["available"]
    conn.commit()

    return available


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
