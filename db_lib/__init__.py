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


def _statements(sql: str) -> list[str]:
    """Split a migration file into individual statements. Our migrations are simple
    DDL with no semicolons inside literals, so strip line comments and split on ';'."""
    body = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def run_migrations(conn: sqlite3.Connection) -> None:
    """Apply any migration files newer than the DB's current user_version.

    Concurrency-safe: a fast unlocked check skips the common already-migrated case,
    then we take the write lock with BEGIN IMMEDIATE and RE-READ user_version inside
    that lock before applying anything. So if two connections open a fresh DB at once,
    only the one that wins the lock migrates; the other re-checks, finds it done, and
    does nothing — no double-applied ALTER. The whole run is one transaction, so a
    failure rolls back cleanly (SQLite DDL is transactional)."""
    files = _migration_files()
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if not files or files[-1][0] <= current:
        return  # nothing pending — don't even take the lock

    prev_isolation = conn.isolation_level
    conn.isolation_level = None  # manual transaction control
    try:
        conn.execute("BEGIN IMMEDIATE")  # acquire the write lock up front
        version = conn.execute("PRAGMA user_version").fetchone()[0]  # re-check under lock
        for number, path in files:
            if number <= version:
                continue
            for statement in _statements(path.read_text()):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {number}")
            version = number
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.isolation_level = prev_isolation
