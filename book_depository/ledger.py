"""Append-only event log for reconstructing the library from logs alone.

Every state-changing action — a book added, a copy added, a borrow, a return — is
emitted as ONE JSON line to the log, tagged with the LEDGER marker. On Render these
lines land in the service logs, a record that lives INDEPENDENTLY of the SQLite file
and the Litestream/R2 backup, so the two can fail separately. If the database is ever
lost beyond what Litestream can restore, replaying these lines in order rebuilds it.

Each line looks like:

    LEDGER {"action": "borrowed", "isbn": "9780...", "loan_id": 42, ...,
            "ts": "2026-06-29T10:00:00.123456+00:00"}

Reconstruction sketch (replay in timestamp order):
  - book_added  -> INSERT the book with its metadata
  - copy_added  -> bump total_count / available for that isbn
  - borrowed    -> INSERT a loan (loan_id links it to its future return)
  - returned    -> mark the matching loan_id returned
"""

import json
import logging
from datetime import datetime, timezone

# Grep this marker out of the Render logs to extract the full event stream.
LEDGER_MARKER = "LEDGER"

_log = logging.getLogger("book_depository.ledger")


def log_event(action: str, **fields) -> None:
    """Emit one reconstruction event as a JSON line. Never raises — a logging
    failure must not break the database write it is recording."""
    try:
        record = {"action": action, **fields, "ts": _now()}
        # ensure_ascii=False keeps Chinese titles readable; sort_keys=True makes the
        # output stable and diff-friendly.
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        _log.info("%s %s", LEDGER_MARKER, line)
    except Exception:  # pragma: no cover - defensive only
        logging.getLogger(__name__).exception("failed to log ledger event %r", action)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
