"""Flask entry point — thin glue. The real logic lives in book_depository/."""

import base64
import csv
import datetime
import hmac
import io
import json
import logging
import os
import re
import secrets
import sqlite3
import time

from flask import Flask, Response, abort, jsonify, render_template, request

from book_depository.db import (
    BACKUP_DIR,
    EDITABLE_FIELDS,
    add_book,
    add_copy,
    all_open_loans,
    backup_db,
    borrow_book,
    close_loan,
    delete_book,
    find_book_by_isbn,
    get_all_books,
    get_db,
    open_loans,
    update_book,
)
from book_depository.i18n import get_strings
from book_depository.isbn import is_valid_isbn13, normalize_isbn, to_isbn13
from book_depository.ledger import log_event
from book_depository.metadata import Book, fetch_book_metadata, search_by_title

# Show timestamped logs in the console. INFO for everything, DEBUG for our own
# package so per-call HTTP statuses (book + author lookups) show up while testing.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("book_depository").setLevel(logging.DEBUG)

app = Flask(__name__)

# No logins yet, so everything lands in one owner's DB. This is the single spot
# that becomes per-user later (e.g. owner from the URL or a session).
DEFAULT_OWNER = "lib_admin"
THEME = os.environ.get("BOOK_THEME", "apple")
TITLE = os.environ.get("BOOK_TITLE", "Library")
_VALID_THEMES = {"apple", "win95", "terminal"}
# UI language for the minimal flow (menu + borrow/return/register messages). Fixed at
# startup. "en" or "zh-Hant".
LANG = os.environ.get("BOOK_LANG", "en")
STRINGS = get_strings(LANG)


def _t(key: str, **kwargs) -> str:
    """Translate a server-message key, filling {placeholders} when given."""
    s = STRINGS.get(key, key)
    return s.format(**kwargs) if kwargs else s


# Set BOOK_PASSWORD on Render to password-protect the whole site.
# Leave unset (or empty) for local LAN use where the network is the perimeter.
BOOK_PASSWORD = os.environ.get("BOOK_PASSWORD", "")
# A SEPARATE, higher-tier secret for editing/deleting records. Unset = admin
# features disabled entirely (the buttons don't even render).
ADMIN_PASSWORD = os.environ.get("BOOK_ADMIN_PASSWORD", "")
# "Trusted local" mode: grant admin to everyone with no password. run_local.py turns
# this ON by default (you own the machine). NEVER set it on a public deploy.
ADMIN_OPEN = os.environ.get("BOOK_ADMIN_OPEN", "").lower() in ("1", "true", "yes", "on")

# Loan period for the "who has what" view: due = borrowed_at + LOAN_DAYS; flag as
# "due soon" within DUE_SOON_DAYS of the due date.
LOAN_DAYS = int(os.environ.get("BOOK_LOAN_DAYS", "30"))
DUE_SOON_DAYS = int(os.environ.get("BOOK_DUE_SOON_DAYS", "3"))

# In-memory brute-force throttle (fine with single-worker gunicorn): too many failed
# password attempts from one IP inside the window → 429 until they age out.
_AUTH_FAILS: dict[str, list[float]] = {}
_AUTH_FAIL_WINDOW = 300  # seconds
_AUTH_FAIL_MAX = 20


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else request.remote_addr) or "?"


def _recent_fails(ip: str) -> list[float]:
    now = time.time()
    fails = [t for t in _AUTH_FAILS.get(ip, []) if now - t < _AUTH_FAIL_WINDOW]
    _AUTH_FAILS[ip] = fails
    return fails


def _auth_locked(ip: str) -> bool:
    return len(_recent_fails(ip)) >= _AUTH_FAIL_MAX


def _note_auth_failure(ip: str) -> None:
    _recent_fails(ip).append(time.time())


def _clean_field(value, limit: int = 500) -> str:
    """Coerce client-supplied metadata to a stripped, length-capped string."""
    return str(value or "").strip()[:limit]


def _csv_safe(value) -> str:
    """Neutralise spreadsheet formula injection: a cell beginning with = + - @ or a
    control char is executed by Excel, so prefix it with a single quote."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _book_key(raw: str):
    """Normalise a URL key for an EXISTING book: a real ISBN → ISBN-13; a manual-entry
    local key (NOISBN-…) passes through so those books can be borrowed/edited/deleted.
    Returns None for anything else."""
    raw = (raw or "").strip()
    if re.fullmatch(r"NOISBN-[0-9A-Fa-f]{1,32}", raw):
        return raw.upper()
    return to_isbn13(raw)


def is_admin() -> bool:
    """True if admin is open (trusted local) or the request carries the right header."""
    if ADMIN_OPEN:
        return True
    if not ADMIN_PASSWORD:
        return False
    supplied = request.headers.get("X-Admin-Password", "")
    if hmac.compare_digest(supplied, ADMIN_PASSWORD):  # constant-time compare
        return True
    if supplied:  # a wrong password was tried — count it toward the lockout
        _note_auth_failure(_client_ip())
    return False


@app.before_request
def check_auth():
    if not BOOK_PASSWORD:
        return
    ip = _client_ip()
    if _auth_locked(ip):
        return Response("Too many failed attempts — try again shortly.", 429)
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8", errors="replace")
            _, _, password = decoded.partition(":")
            if hmac.compare_digest(password, BOOK_PASSWORD):  # constant-time
                return
        except Exception:
            pass
    _note_auth_failure(ip)
    return Response(
        "Library access is password-protected.",
        401,
        {"WWW-Authenticate": 'Basic realm="Library"'},
    )


# CSRF defence: state-changing requests must carry this custom header. A cross-origin
# page can't add it on a plain form/navigation, and setting it from a cross-origin fetch
# triggers a preflight this server never approves — so only our own page can mutate.
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


@app.before_request
def csrf_guard():
    if request.method in _MUTATING and not request.headers.get("X-Requested-With"):
        abort(403, "Missing X-Requested-With header.")


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    # A conservative CSP that doesn't touch script/img sources (so the CDN barcode
    # polyfill + its WASM keep working): block plugins, framing, and <base> hijacking.
    resp.headers.setdefault(
        "Content-Security-Policy",
        "frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
    )
    if request.is_secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return resp


@app.errorhandler(400)
@app.errorhandler(401)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(409)
def json_error(err):
    """Return errors as JSON so the scan page can show them."""
    return jsonify(error=err.description), err.code


@app.get("/")
def index():
    """Serve the scan page (camera lives in the browser)."""
    theme = THEME if THEME in _VALID_THEMES else "apple"
    return render_template(
        "scan.html",
        theme=theme,
        title=TITLE,
        admin_enabled=ADMIN_OPEN or bool(ADMIN_PASSWORD),
        admin_open=ADMIN_OPEN,
        lang=LANG,
        strings=STRINGS,
    )


@app.post("/api/admin/check")
def admin_check():
    """Validate an admin password so the frontend can unlock edit/delete."""
    if not is_admin():
        abort(401, "Wrong admin password.")
    return jsonify(ok=True)


@app.get("/api/lookup/<raw_isbn>")
def lookup(raw_isbn: str):
    """The browser sends a scanned barcode here; we return normalized book JSON.

    This is the seam between the browser slice and the Python body. Keep it dumb:
    normalize -> validate -> fetch -> return. (The register flow that WRITES to the
    DB is a separate route you'll add later — see the README.)
    """
    isbn = to_isbn13(raw_isbn)
    if isbn is None:
        abort(400, description=_t("srv_invalid_isbn", raw=raw_isbn))

    book = fetch_book_metadata(isbn)
    if book is None:
        abort(404, description=_t("srv_no_book"))

    return jsonify(book.to_dict())


@app.get("/api/search")
def search():
    """Search candidates by title (or title + author). Returns a list for the user to
    pick from; picking one then registers it by its ISBN via the normal flow."""
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify(results=[])
    results = search_by_title(query)
    return jsonify(results=[b.to_dict() for b in results])


@app.post("/api/register/<raw_isbn>")
def register(raw_isbn: str):
    isbn = to_isbn13(raw_isbn)
    if isbn is None:
        abort(400, description=_t("srv_invalid_isbn", raw=raw_isbn))

    # `?confirm=true` means the user already saw the "add a copy?" dialog and said yes.
    confirm = request.args.get("confirm") == "true"

    conn = get_db(DEFAULT_OWNER)
    try:
        existing = find_book_by_isbn(conn, isbn)
        if not existing:
            posted = request.get_json(silent=True)
            if posted:
                # This path takes client-supplied metadata (the frontend posts back
                # what it looked up) and isn't admin-gated, so coerce/strip/cap every
                # field rather than storing it verbatim.
                meta = Book(
                    **{
                        k: _clean_field(posted.get(k, ""))
                        for k in (
                            "isbn",
                            "title",
                            "author",
                            "cover_url",
                            "publisher",
                            "year",
                            "language",
                            "source",
                        )
                    }
                )
            else:
                meta = fetch_book_metadata(isbn)
            if meta is None:
                abort(404, _t("srv_no_book"))
            # Always store under the validated/converted ISBN-13, never the
            # client-supplied one — keeps the key consistent with de-dup above
            # and with borrow/return (which also convert to ISBN-13).
            meta.isbn = isbn
            try:
                add_book(conn, meta)
            except sqlite3.IntegrityError:
                # Another request registered this same ISBN a moment ago (the UNIQUE
                # constraint caught the race). Treat it as already-registered.
                conn.rollback()
                fresh = find_book_by_isbn(conn, isbn)
                return jsonify(status="exists", book=dict(fresh))
            log_event(
                "book_added",
                isbn=meta.isbn,
                title=meta.title,
                author=meta.author,
                cover_url=meta.cover_url,
                publisher=meta.publisher,
                year=meta.year,
                source=meta.source,
            )
            backup_db(conn, BACKUP_DIR)
            return jsonify(status="added", book=meta.to_dict())

        if not confirm:
            # already registered — let the frontend ask "add a copy?"
            return jsonify(status="exists", book=dict(existing))

        add_copy(conn, isbn)
        fresh = find_book_by_isbn(conn, isbn)  # re-read for the updated counts
        log_event("copy_added", isbn=isbn, total_count=fresh["total_count"])
        backup_db(conn, BACKUP_DIR)
        return jsonify(status="copy_added", book=dict(fresh))
    finally:
        conn.close()


def _synthetic_isbn() -> str:
    """A unique local key for a book with no ISBN. Clearly not a real ISBN."""
    return "NOISBN-" + secrets.token_hex(5).upper()


@app.post("/api/register-manual")
def register_manual():
    """Admin: add a book by hand — for books with no ISBN, or ones not found online.
    ISBN is optional (a local key is generated if omitted); only title is required."""
    if not is_admin():
        abort(403, "Admin access required.")
    data = request.get_json(silent=True) or {}

    title = (data.get("title") or "").strip()
    if not title:
        abort(400, _t("srv_title_required"))

    raw_isbn = (data.get("isbn") or "").strip()
    if raw_isbn:
        isbn = to_isbn13(raw_isbn)
        if isbn is None:
            abort(400, description=_t("srv_invalid_isbn", raw=raw_isbn))
    else:
        isbn = _synthetic_isbn()

    try:
        copies = max(1, int(data.get("total_count") or 1))
    except (TypeError, ValueError):
        copies = 1

    book = Book(
        isbn=isbn,
        title=title,
        author=(data.get("author") or "").strip(),
        publisher=(data.get("publisher") or "").strip(),
        year=str(data.get("year") or "").strip(),
        language=(data.get("language") or "").strip(),
        cover_url=(data.get("cover_url") or "").strip(),
        source="MANUAL",
    )

    conn = get_db(DEFAULT_OWNER)
    try:
        existing = find_book_by_isbn(conn, isbn)
        if existing:
            return jsonify(status="exists", book=dict(existing))
        try:
            add_book(conn, book)
        except sqlite3.IntegrityError:
            conn.rollback()
            return jsonify(status="exists", book=dict(find_book_by_isbn(conn, isbn)))
        for _ in range(copies - 1):  # extra copies beyond the first
            add_copy(conn, isbn)
        fresh = find_book_by_isbn(conn, isbn)
        log_event(
            "book_added", isbn=isbn, title=title, author=book.author,
            publisher=book.publisher, year=book.year, source="MANUAL",
        )
        backup_db(conn, BACKUP_DIR)
        return jsonify(status="added", book=dict(fresh))
    finally:
        conn.close()


@app.get("/api/book/<raw_isbn>")
def book_lookup(raw_isbn: str):
    """
    DB lookup for borrow/return — NO network call (metadata was cached at register).
    Returns the book AND its open loans (the return screen lists those to pick from).
    """
    isbn = _book_key(raw_isbn)
    if isbn is None:
        abort(400, description=_t("srv_invalid_isbn", raw=raw_isbn))
    conn = get_db(DEFAULT_OWNER)
    try:
        row = find_book_by_isbn(conn, isbn)
        if row is None:
            abort(404, _t("srv_not_in_library"))
        loans = [dict(l) for l in open_loans(conn, row["book_id"])]
    finally:
        conn.close()

    return jsonify(book=dict(row), open_loans=loans)


@app.post("/api/borrow/<raw_isbn>")
def borrow(raw_isbn: str):
    """
    Borrow a copy under a minimal borrower label. JSON body: {"borrower": ...}.
    """
    isbn = _book_key(raw_isbn)
    if isbn is None:
        abort(400, description=_t("srv_invalid_isbn", raw=raw_isbn))
    conn = get_db(DEFAULT_OWNER)
    try:
        row = find_book_by_isbn(conn, isbn)
        if row is None:
            abort(404, _t("srv_not_in_library"))
        borrower = (request.get_json(silent=True) or {}).get("borrower", "").strip()
        if not borrower:
            abort(400, _t("srv_borrower_required"))
        loan_id = borrow_book(conn, row["book_id"], borrower)
        if loan_id is None:
            abort(409, _t("srv_no_copies"))
        log_event("borrowed", isbn=isbn, loan_id=loan_id, borrower=borrower)
        fresh = find_book_by_isbn(conn, isbn)
        backup_db(conn, BACKUP_DIR)
    finally:
        conn.close()

    return jsonify(status="borrowed", book=dict(fresh))


@app.post("/api/return/<raw_isbn>")
def return_book_route(raw_isbn: str):
    """
    Close a specific open loan the user picked. JSON body: {"loan_id": ...}.
    """
    isbn = _book_key(raw_isbn)
    if isbn is None:
        abort(400, description=_t("srv_invalid_isbn", raw=raw_isbn))
    conn = get_db(DEFAULT_OWNER)
    try:
        loan_id = (request.get_json(silent=True) or {}).get("loan_id")
        if not loan_id:
            abort(400, _t("srv_pick_loan"))
        available = close_loan(conn, loan_id)
        if available is None:
            abort(409, _t("srv_already_returned"))
        log_event("returned", isbn=isbn, loan_id=loan_id)
        fresh = find_book_by_isbn(conn, isbn)
        backup_db(conn, BACKUP_DIR)
    finally:
        conn.close()

    return jsonify(status="returned", book=dict(fresh))


@app.patch("/api/book/<raw_isbn>")
def edit_book(raw_isbn: str):
    """Admin: edit a book's metadata / copy count. JSON body = fields to change."""
    if not is_admin():
        abort(403, "Admin access required.")
    isbn = _book_key(raw_isbn)
    if isbn is None:
        abort(400, description=_t("srv_invalid_isbn", raw=raw_isbn))
    payload = request.get_json(silent=True) or {}
    conn = get_db(DEFAULT_OWNER)
    try:
        updated = update_book(conn, isbn, payload)
        if updated is None:
            abort(404, _t("srv_not_in_library"))
        changed = {k: payload[k] for k in payload if k in EDITABLE_FIELDS}
        log_event("book_edited", isbn=isbn, fields=changed)
        backup_db(conn, BACKUP_DIR)
    finally:
        conn.close()
    return jsonify(status="updated", book=dict(updated))


@app.delete("/api/book/<raw_isbn>")
def remove_book(raw_isbn: str):
    """Admin: delete a book and its loan history."""
    if not is_admin():
        abort(403, "Admin access required.")
    isbn = _book_key(raw_isbn)
    if isbn is None:
        abort(400, description=_t("srv_invalid_isbn", raw=raw_isbn))
    conn = get_db(DEFAULT_OWNER)
    try:
        if not delete_book(conn, isbn):
            abort(404, _t("srv_not_in_library"))
        log_event("book_deleted", isbn=isbn)
        backup_db(conn, BACKUP_DIR)
    finally:
        conn.close()
    return jsonify(status="deleted", isbn=isbn)


@app.get("/api/books")
def list_books():
    conn = get_db(DEFAULT_OWNER)
    try:
        books = get_all_books(conn)
    finally:
        conn.close()
    return jsonify(books=[dict(b) for b in books])


# Columns to include in an export (skip book_id — an internal autoincrement).
_EXPORT_COLUMNS = (
    "isbn", "title", "author", "publisher", "year", "language",
    "total_count", "available", "source", "added_at", "cover_url",
)


@app.get("/api/export.csv")
def export_csv():
    """Download the whole catalog as CSV (UTF-8 BOM so Excel reads Chinese)."""
    conn = get_db(DEFAULT_OWNER)
    try:
        books = get_all_books(conn)
    finally:
        conn.close()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_EXPORT_COLUMNS)
    for b in books:
        keys = b.keys()
        writer.writerow([_csv_safe(b[c]) if c in keys else "" for c in _EXPORT_COLUMNS])
    return Response(
        buf.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",  # Flask appends "; charset=utf-8"
        headers={"Content-Disposition": "attachment; filename=library.csv"},
    )


@app.get("/api/export.json")
def export_json():
    """Download the whole catalog as JSON."""
    conn = get_db(DEFAULT_OWNER)
    try:
        books = [dict(b) for b in get_all_books(conn)]
    finally:
        conn.close()
    return Response(
        json.dumps(books, ensure_ascii=False, indent=2),
        mimetype="application/json; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=library.json"},
    )


# CSV columns we import (mirrors the export). available/added_at/source are derived or
# system-owned, so they're ignored on import.
_IMPORT_FIELDS = ("title", "author", "publisher", "year", "language", "cover_url")


@app.post("/api/import.csv")
def import_csv():
    """Admin: upsert books from a CSV (same columns as the export). Existing ISBNs are
    updated (non-empty cells only); new ones are added. Rows without a title are skipped."""
    if not is_admin():
        abort(403, "Admin access required.")
    upload = request.files.get("file")
    if upload is None:
        abort(400, description=_t("srv_no_file"))
    try:
        text = upload.read().decode("utf-8-sig")  # tolerate the BOM our export writes
    except UnicodeDecodeError:
        abort(400, description=_t("srv_bad_csv"))

    added = updated = skipped = 0
    conn = get_db(DEFAULT_OWNER)
    try:
        for row in csv.DictReader(io.StringIO(text)):
            title = (row.get("title") or "").strip()
            if not title:
                skipped += 1
                continue
            raw_isbn = (row.get("isbn") or "").strip()
            isbn = to_isbn13(raw_isbn) if raw_isbn else _synthetic_isbn()
            if isbn is None:  # an ISBN was given but it's invalid
                skipped += 1
                continue
            fields = {"title": title}
            fields.update(
                {f: (row.get(f) or "").strip() for f in _IMPORT_FIELDS if f != "title"}
            )
            try:
                copies = max(1, int(row.get("total_count") or 1))
            except (TypeError, ValueError):
                copies = 1

            if find_book_by_isbn(conn, isbn):
                # only overwrite with non-empty cells, so blanks don't wipe good data
                updates = {k: v for k, v in fields.items() if v}
                updates["total_count"] = copies
                update_book(conn, isbn, updates)
                updated += 1
            else:
                book = Book(isbn=isbn, source="IMPORT", **fields)
                try:
                    add_book(conn, book)
                except sqlite3.IntegrityError:
                    conn.rollback()
                    skipped += 1
                    continue
                for _ in range(copies - 1):
                    add_copy(conn, isbn)
                added += 1

        log_event("csv_import", added=added, updated=updated, skipped=skipped)
        backup_db(conn, BACKUP_DIR)
    finally:
        conn.close()
    return jsonify(status="imported", added=added, updated=updated, skipped=skipped)


def _parse_ts(value: str):
    """Parse a stored SQLite timestamp ('YYYY-MM-DD HH:MM:SS', UTC) to an aware dt."""
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt


@app.get("/api/loans")
def loans():
    """Admin: every book currently on loan, with a computed due date and status
    (ok / due_soon / overdue) so the frontend can highlight them."""
    if not is_admin():
        abort(403, "Admin access required.")
    conn = get_db(DEFAULT_OWNER)
    try:
        rows = all_open_loans(conn)
    finally:
        conn.close()

    now = datetime.datetime.now(datetime.timezone.utc)
    soon = datetime.timedelta(days=DUE_SOON_DAYS)
    out = []
    for r in rows:
        borrowed = _parse_ts(r["borrowed_at"])
        due = borrowed + datetime.timedelta(days=LOAN_DAYS) if borrowed else None
        status, days_left = "ok", None
        if due:
            days_left = (due - now).days
            if now > due:
                status = "overdue"
            elif due - now <= soon:
                status = "due_soon"
        out.append(
            {
                "loan_id": r["loan_id"],
                "isbn": r["isbn"],
                "title": r["title"],
                "borrower": r["borrower"],
                "borrowed_at": (r["borrowed_at"] or "")[:10],
                "due_at": due.strftime("%Y-%m-%d") if due else "",
                "days_left": days_left,
                "status": status,
            }
        )
    return jsonify(loans=out, loan_days=LOAN_DAYS)


if __name__ == "__main__":
    # localhost counts as a secure context, so the camera works here on your laptop.
    # Phone testing over a LAN IP is BLOCKED without HTTPS — deploy, or use a tunnel
    # (e.g. `cloudflared tunnel`), once you want to test on a real phone.
    # No debug=True: the Werkzeug debugger is an RCE console to anyone on the network.
    # Keep auto-reload for local dev, but never the interactive debugger.
    app.run(host="0.0.0.0", port=8000, use_reloader=True, use_debugger=False, threaded=True)
