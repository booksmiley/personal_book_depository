-- 001: initial schema.
--
-- Mirrors the schema the app shipped with, using IF NOT EXISTS so it is a no-op
-- on databases that already have these tables (they just get stamped to v1),
-- while still creating everything for a brand-new empty database.

CREATE TABLE IF NOT EXISTS books (
    book_id     INTEGER PRIMARY KEY,
    isbn        TEXT UNIQUE NOT NULL,
    title       TEXT NOT NULL,
    author      TEXT,
    cover_url   TEXT,
    publisher   TEXT,
    year        TEXT,
    source      TEXT,
    total_count INTEGER NOT NULL DEFAULT 1,
    available   INTEGER NOT NULL DEFAULT 1
);

-- One row per borrow. `returned_at IS NULL` means the copy is still out.
CREATE TABLE IF NOT EXISTS loans (
    loan_id     INTEGER PRIMARY KEY,
    book_id     INTEGER NOT NULL REFERENCES books(book_id),
    borrower    TEXT NOT NULL,
    borrowed_at TEXT NOT NULL DEFAULT (datetime('now')),
    returned_at TEXT
);
