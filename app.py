"""Flask entry point — thin glue. The real logic lives in book_depository/."""

from flask import Flask, abort, jsonify, render_template

from book_depository.isbn import is_valid_isbn13, normalize_isbn
from book_depository.metadata import fetch_book_metadata

app = Flask(__name__)


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


if __name__ == "__main__":
    # localhost counts as a secure context, so the camera works here on your laptop.
    # Phone testing over a LAN IP is BLOCKED without HTTPS — deploy, or use a tunnel
    # (e.g. `cloudflared tunnel`), once you want to test on a real phone.
    app.run(host="0.0.0.0", port=8000, debug=True)
