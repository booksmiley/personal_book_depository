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
DATA_DIR = Path(os.environ.get("BOOK_DATA_DIR") or Path(__file__).resolve().parent.parent / "data")

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

CREATE TABLE IF NOT EXISTS contacts (
    contact_id INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    contact    TEXT
);

CREATE TABLE IF NOT EXISTS status (
    event_id        INTEGER PRIMARY KEY,
    book_id         INTEGER NOT NULL REFERENCES books(book_id),
    act             TEXT NOT NULL CHECK (act IN ('borrow', 'return')),
    ts              TEXT NOT NULL DEFAULT (datetime('now')),
    borrower_id     INTEGER REFERENCES contacts(contact_id),
    available_after INTEGER
);
"""


BOOK_QUERY_BY_ISBN = "SELECT * FROM books WHERE isbn = ?"
BOOK_INSERT = "INSERT INTO books (isbn, title, author, cover_url, publisher, year) VALUES (?, ?, ?, ?, ?, ?)"
BOOK_UPDATE_COPY = (
    "UPDATE books SET total_count = total_count + 1, available = available + 1 "
    "WHERE isbn = ?"
)


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
