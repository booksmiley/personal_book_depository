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
from datetime import date, datetime, timezone
from pathlib import Path

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


class _DailyFileHandler(logging.Handler):
    """Write each event to YYYY-MM-DD.log, switching files when the date rolls over.
    Volume is tiny, so we just flush every line."""

    def __init__(self, directory: Path):
        super().__init__()
        self._dir = directory
        self._day = None
        self._stream = None

    def _today_stream(self):
        today = date.today().isoformat()
        if today != self._day:
            if self._stream:
                self._stream.close()
            self._stream = open(self._dir / f"{today}.log", "a", encoding="utf-8")
            self._day = today
        return self._stream

    def emit(self, record):
        try:
            stream = self._today_stream()
            stream.write(self.format(record) + "\n")
            stream.flush()
        except Exception:
            self.handleError(record)


def enable_file_logging(directory) -> None:
    """Persist ledger events to date-split files (YYYY-MM-DD.log) in `directory`.
    For LOCAL runs only — on Render the same lines already land in the platform logs.
    Console output is unaffected (this is an extra handler)."""
    path = Path(directory).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    handler = _DailyFileHandler(path)
    handler.setFormatter(logging.Formatter("%(message)s"))  # one "LEDGER {json}" per line
    _log.addHandler(handler)
    _log.setLevel(logging.INFO)  # ensure ledger events pass to the handler


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
