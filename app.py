"""Quart (async) entry point — thin glue. The real logic lives in book_depository/.

Async so a slow metadata lookup (network I/O) yields the event loop and other
requests (borrow/return) keep flowing. The DB stays sync sqlite — its calls are
sub-millisecond, safe to run directly on the loop; only the network is awaited.
"""

import base64
import hmac
import logging
import os
import sqlite3

from quart import Quart, Response, abort, jsonify, render_template, request

from book_depository.db import (
    BACKUP_DIR,
    EDITABLE_FIELDS,
    add_book,
    add_copy,
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
from book_depository.metadata import Book, fetch_book_metadata

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("book_depository").setLevel(logging.DEBUG)

app = Quart(__name__)

# No logins yet, so everything lands in one owner's DB. This is the single spot
# that becomes per-user later (e.g. owner from the URL or a session).
DEFAULT_OWNER = "lib_admin"
THEME = os.environ.get("BOOK_THEME", "apple")
TITLE = os.environ.get("BOOK_TITLE", "Library")
_VALID_THEMES = {"apple", "win95", "terminal"}
# UI language for the minimal flow (menu + borrow/return/register messages).
LANG = os.environ.get("BOOK_LANG", "en")
STRINGS = get_strings(LANG)


def _t(key: str, **kwargs) -> str:
    """Translate a server-message key, filling {placeholders} when given."""
    s = STRINGS.get(key, key)
    return s.format(**kwargs) if kwargs else s


# Set BOOK_PASSWORD to password-protect the whole site; unset = open (LAN use).
BOOK_PASSWORD = os.environ.get("BOOK_PASSWORD", "")
# A SEPARATE, higher-tier secret for editing/deleting records. Unset = admin off.
ADMIN_PASSWORD = os.environ.get("BOOK_ADMIN_PASSWORD", "")
# "Trusted local" mode: grant admin to everyone with no password. run_local.py turns
# this ON by default (you own the machine). NEVER set it on a public deploy.
ADMIN_OPEN = os.environ.get("BOOK_ADMIN_OPEN", "").lower() in ("1", "true", "yes", "on")


def is_admin() -> bool:
    """True if admin is open (trusted local) or the request carries the right header."""
    if ADMIN_OPEN:
        return True
    if not ADMIN_PASSWORD:
        return False
    supplied = request.headers.get("X-Admin-Password", "")
    return hmac.compare_digest(supplied, ADMIN_PASSWORD)  # constant-time compare


@app.before_request
async def check_auth():
    if not BOOK_PASSWORD:
        return
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8", errors="replace")
            _, _, password = decoded.partition(":")
            if password == BOOK_PASSWORD:
                return
        except Exception:
            pass
    return Response(
        "Library access is password-protected.",
        401,
        {"WWW-Authenticate": 'Basic realm="Library"'},
    )


@app.errorhandler(400)
@app.errorhandler(401)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(409)
async def json_error(err):
    """Return errors as JSON so the scan page can show them."""
    return jsonify(error=err.description), err.code


@app.get("/")
async def index():
    """Serve the scan page (camera lives in the browser)."""
    theme = THEME if THEME in _VALID_THEMES else "apple"
    return await render_template(
        "scan.html",
        theme=theme,
        title=TITLE,
        admin_enabled=ADMIN_OPEN or bool(ADMIN_PASSWORD),
        admin_open=ADMIN_OPEN,
        lang=LANG,
        strings=STRINGS,
    )


@app.post("/api/admin/check")
async def admin_check():
    """Validate an admin password so the frontend can unlock edit/delete."""
    if not is_admin():
        abort(401, "Wrong admin password.")
    return jsonify(ok=True)


@app.get("/api/lookup/<raw_isbn>")
async def lookup(raw_isbn: str):
    """The browser sends a scanned barcode here; we return normalized book JSON."""
    isbn = to_isbn13(raw_isbn)
    if isbn is None:
        abort(400, description=_t("srv_invalid_isbn", raw=raw_isbn))

    book = await fetch_book_metadata(isbn)
    if book is None:
        abort(404, description=_t("srv_no_book"))

    return jsonify(book.to_dict())


@app.post("/api/register/<raw_isbn>")
async def register(raw_isbn: str):
    isbn = to_isbn13(raw_isbn)
    if isbn is None:
        abort(400, description=_t("srv_invalid_isbn", raw=raw_isbn))

    # `?confirm=true` means the user already saw the "add a copy?" dialog and said yes.
    confirm = request.args.get("confirm") == "true"

    conn = get_db(DEFAULT_OWNER)
    try:
        existing = find_book_by_isbn(conn, isbn)
        if not existing:
            posted = await request.get_json(silent=True)
            if posted:
                meta = Book(
                    **{
                        k: posted.get(k, "")
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
                meta = await fetch_book_metadata(isbn)
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


@app.get("/api/book/<raw_isbn>")
async def book_lookup(raw_isbn: str):
    """DB lookup for borrow/return — NO network call (metadata cached at register).
    Returns the book AND its open loans (the return screen lists those to pick from)."""
    isbn = to_isbn13(raw_isbn)
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
async def borrow(raw_isbn: str):
    """Borrow a copy under a minimal borrower label. JSON body: {"borrower": ...}."""
    isbn = to_isbn13(raw_isbn)
    if isbn is None:
        abort(400, description=_t("srv_invalid_isbn", raw=raw_isbn))
    conn = get_db(DEFAULT_OWNER)
    try:
        row = find_book_by_isbn(conn, isbn)
        if row is None:
            abort(404, _t("srv_not_in_library"))
        borrower = ((await request.get_json(silent=True)) or {}).get("borrower", "").strip()
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
async def return_book_route(raw_isbn: str):
    """Close a specific open loan the user picked. JSON body: {"loan_id": ...}."""
    isbn = to_isbn13(raw_isbn)
    if isbn is None:
        abort(400, description=_t("srv_invalid_isbn", raw=raw_isbn))
    conn = get_db(DEFAULT_OWNER)
    try:
        loan_id = ((await request.get_json(silent=True)) or {}).get("loan_id")
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
async def edit_book(raw_isbn: str):
    """Admin: edit a book's metadata / copy count. JSON body = fields to change."""
    if not is_admin():
        abort(403, "Admin access required.")
    isbn = to_isbn13(raw_isbn)
    if isbn is None:
        abort(400, description=_t("srv_invalid_isbn", raw=raw_isbn))
    payload = (await request.get_json(silent=True)) or {}
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
async def remove_book(raw_isbn: str):
    """Admin: delete a book and its loan history."""
    if not is_admin():
        abort(403, "Admin access required.")
    isbn = to_isbn13(raw_isbn)
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
async def list_books():
    conn = get_db(DEFAULT_OWNER)
    try:
        books = get_all_books(conn)
    finally:
        conn.close()
    return jsonify(books=[dict(b) for b in books])


if __name__ == "__main__":
    # localhost counts as a secure context, so the camera works here on your laptop.
    # Phone testing over a LAN IP is BLOCKED without HTTPS — deploy or use a tunnel.
    app.run(host="0.0.0.0", port=8000, debug=True)
