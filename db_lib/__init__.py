"""Schema migrations: numbered SQL files applied in order, tracked by user_version.

Convention: each file here is named NNN_description.sql (zero-padded). On every
connection we read the database's PRAGMA user_version and apply every file whose
number is HIGHER than that, in ascending order. After a file applies cleanly we bump
user_version to that file's number.

Why this works across deploys: a DB created last month sits at (say) version 2. Ship
a new 003_*.sql and on next open only 003 runs. A brand-new empty DB sits at version 0
and gets 001, 002, 003 in sequence. No file ever runs twice.
"""

import re
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent

# Matches "001", "002", ... at the start of a filename; the rest is just a label.
_NUMBER = re.compile(r"^(\d+)_.*\.sql$")


def _migration_files() -> list[tuple[int, Path]]:
    """All migration files as (number, path), sorted by number."""
    found = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        m = _NUMBER.match(path.name)
        if m:
            found.append((int(m.group(1)), path))
    found.sort(key=lambda pair: pair[0])
    return found


def run_migrations(conn: sqlite3.Connection) -> None:
    """Apply any migration files newer than the DB's current user_version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]

    for number, path in _migration_files():
        if number <= version:
            continue

        # Wrap the file in one transaction so the DDL and the version bump commit
        # together. SQLite DDL is transactional, so if anything in the file raises,
        # the whole thing (including the user_version change) rolls back and the
        # next start retries cleanly from the same version.
        #
        # Note: executescript() issues an implicit COMMIT first, but we open a fresh
        # connection with nothing pending, so that is a harmless no-op here.
        body = path.read_text()
        conn.executescript(
            f"BEGIN;\n{body}\nPRAGMA user_version = {number};\nCOMMIT;"
        )
