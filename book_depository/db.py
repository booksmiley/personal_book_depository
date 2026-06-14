"""Persistence — SQLite, one file per owner ("DB-per-owner" from the brief).

Nothing in the scan->lookup slice touches this module. It comes into play in the
*register* flow, when the user confirms a scanned book should be added.

Why SQLite + stdlib `sqlite3`: no server, one file per owner sidesteps multi-tenancy
entirely, and it's all own-able code — ideal for practice. On a real deploy the file
needs to sit on a PERSISTENT disk/volume or it gets wiped on redeploy.
"""

import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

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


def get_db(owner: str) -> sqlite3.Connection:
    """Open (creating if needed) the SQLite file for one owner."""
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DATA_DIR / f"{owner}.sqlite")
    conn.row_factory = sqlite3.Row          # rows behave like dicts
    conn.execute("PRAGMA foreign_keys = ON")  # sqlite needs this per-connection
    conn.executescript(SCHEMA)              # idempotent — safe to run every open
    return conn


# --- Your exercises (the register/borrow/return flows write through these) ---

def find_book_by_isbn(conn: sqlite3.Connection, isbn: str):
    """TODO: SELECT * FROM books WHERE isbn = ?  -> return the row or None."""
    raise NotImplementedError

def add_book(conn, book) -> int:
    """TODO: INSERT a new books row from a metadata.Book (total_count/available = 1).

    The register flow first checks find_book_by_isbn: if it exists, show the
    "already registered — add a copy?" alert and increment counts instead of inserting.
    """
    raise NotImplementedError
