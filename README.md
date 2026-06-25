# Personal Book Depository

A lean open source library system for a small church library. Scan a barcode with your
phone camera → register, borrow, or return books. No app install needed — runs in the
browser over HTTPS.

Stack: Python + Flask + SQLite (one file per owner), zero-build JS frontend.

## Architecture

```
browser (thin)                     python
──────────────                     ──────────────────────────────────────
camera + decode EAN-13   ──GET──>  /api/lookup/<isbn>
  static/scan.js                     book_depository/isbn.py    (validate)
                                      book_depository/metadata.py (fetch)

Register mode            ──POST─>  /api/register/<isbn>
                                     book_depository/db.py       (write)

Borrow mode              ──GET──>  /api/book/<isbn>      (open loans)
                         ──POST─>  /api/borrow/<isbn>    (borrow copy)

Return mode              ──GET──>  /api/book/<isbn>      (list to pick from)
                         ──POST─>  /api/return/<isbn>    (close loan)
```

All routes share one normalized `Book` object (see `metadata.py`). The DB is SQLite,
one file per owner; `loans` tracks individual borrows with open/closed state.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py          # http://localhost:8000
```

`localhost` is a secure context, so the camera works on your laptop. To test on a
phone you need HTTPS — deploy to Render (`render.yaml` included), or run the local
HTTPS server:

```bash
pip install -r requirements-local.txt
python run_local.py    # reads local_config/config.yml for cert + data dir
```

## What's built

- **Scan**: camera + EAN-13 decode, ISBN-13 validation, metadata via Open Library with
  Google Books fallback.
- **Register**: two-step flow (new book or add a copy), duplicate detection.
- **Borrow**: records a named borrower against a copy; atomic guard prevents
  over-borrowing.
- **Return**: scan → see open loans → tap the exact loan to close. Not FIFO.
- **Deploy**: Render config included. Free tier data is ephemeral; see TODO for the
  iCloud backup plan.
