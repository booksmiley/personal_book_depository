"""Flask entry point — thin glue. The real logic lives in book_depository/."""

import logging

from flask import Flask, abort, jsonify, render_template, request

from book_depository.db import add_book, add_copy, find_book_by_isbn, get_db
from book_depository.isbn import is_valid_isbn13, normalize_isbn
from book_depository.metadata import fetch_book_metadata

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


@app.errorhandler(400)
@app.errorhandler(404)
def json_error(err):
    """Return errors as JSON so the scan page can show them."""
    return jsonify(error=err.description), err.code


@app.get("/")
def index():
    """Serve the scan page (camera lives in the browser)."""
    return render_template("scan.html")


@app.get("/api/lookup/<raw_isbn>")
def lookup(raw_isbn: str):
    """The browser sends a scanned barcode here; we return normalized book JSON.

    This is the seam between the browser slice and the Python body. Keep it dumb:
    normalize -> validate -> fetch -> return. (The register flow that WRITES to the
    DB is a separate route you'll add later — see the README.)
    """
    isbn = normalize_isbn(raw_isbn)
    if not is_valid_isbn13(isbn):
        abort(400, description=f"Not a valid ISBN-13: {raw_isbn!r}")

    book = fetch_book_metadata(isbn)
    if book is None:
        abort(404, description=f"No metadata found for {isbn}")

    return jsonify(book.to_dict())


@app.post("/api/register/<raw_isbn>")
def register(raw_isbn: str):
    isbn = normalize_isbn(raw_isbn)
    if not is_valid_isbn13(isbn):
        abort(400, description=f"Not a valid ISBN-13: {raw_isbn!r}")

    # `?confirm=true` means the user already saw the "add a copy?" dialog and said yes.
    confirm = request.args.get("confirm") == "true"

    conn = get_db(DEFAULT_OWNER)
    try:
        existing = find_book_by_isbn(conn, isbn)
        if not existing:
            meta = fetch_book_metadata(isbn)  # the ONLY network call
            if meta is None:
                abort(404, "no book is found online")
            add_book(conn, meta)
            return jsonify(status="added", book=meta.to_dict())

        if not confirm:
            # already registered — let the frontend ask "add a copy?"
            return jsonify(status="exists", book=dict(existing))

        add_copy(conn, isbn)
        fresh = find_book_by_isbn(conn, isbn)  # re-read for the updated counts
        return jsonify(status="copy_added", book=dict(fresh))
    finally:
        conn.close()


if __name__ == "__main__":
    # localhost counts as a secure context, so the camera works here on your laptop.
    # Phone testing over a LAN IP is BLOCKED without HTTPS — deploy, or use a tunnel
    # (e.g. `cloudflared tunnel`), once you want to test on a real phone.
    app.run(host="0.0.0.0", port=8000, debug=True)
