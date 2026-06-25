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

# Where the per-owner SQLite files live. Defaults to ./data inside the repo (git
# -ignored), but BOOK_DATA_DIR overrides it so local runs can keep personal library
# data in a safe location outside the project. run_local.py sets this from config.yml.
DATA_DIR = Path(
    os.environ.get("BOOK_DATA_DIR") or Path(__file__).resolve().parent.parent / "data"
)
BACKUP_DIR = os.environ.get("BOOK_BACKUP_DIR", "")

# One source of truth for the schema. Open question from the brief: store `available`
# or derive it from the ledger. This scaffold STORES it (simplest); revisit later.
SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    book_id     INTEGER PRIMARY KEY,
    isbn        TEXT UNIQUE NOT NULL,
    title       TEXT NOT NULL,
    author      TEXT,
    cover_url   TEXT,
    publisher   TEXT,
    year        TEXT,
    total_count INTEGER NOT NULL DEFAULT 1,
    available   INTEGER NOT NULL DEFAULT 1
);

-- One row per borrow. `returned_at IS NULL` means the copy is still out, so a
-- book's open loans are just its rows with a null returned_at. Minimal borrower
-- info for now (a label); a `contact` column can be added later when we have
-- secure storage. This replaces the old status/contacts tables.
CREATE TABLE IF NOT EXISTS loans (
    loan_id     INTEGER PRIMARY KEY,
    book_id     INTEGER NOT NULL REFERENCES books(book_id),
    borrower    TEXT NOT NULL,
    borrowed_at TEXT NOT NULL DEFAULT (datetime('now')),
    returned_at TEXT
);
"""


BOOK_QUERY_BY_ISBN = "SELECT * FROM books WHERE isbn = ?"
BOOK_INSERT = "INSERT INTO books (isbn, title, author, cover_url, publisher, year) VALUES (?, ?, ?, ?, ?, ?)"
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
    conn.executescript(SCHEMA)  # idempotent — safe to run every open
    return conn


def find_book_by_isbn(conn: sqlite3.Connection, isbn: str):
    cur = conn.execute(BOOK_QUERY_BY_ISBN, (isbn,))
    row = cur.fetchone()
    return row


def add_book(conn: sqlite3.Connection, book) -> int:
    cur = conn.execute(
        BOOK_INSERT,
        (book.isbn, book.title, book.author, book.cover_url, book.publisher, book.year),
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
