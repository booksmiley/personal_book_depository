# Personal Book Depository

A lean open source library system. This repo currently scaffolds **one slice**:
*scan a barcode → ISBN → fetch book metadata*. See `personal-library-brief.md` for the
full design.

## Architecture of this slice

```
browser (thin)                     python (the main body)
──────────────                     ──────────────────────
camera + decode EAN-13   ──GET──>  /api/lookup/<isbn>
  static/scan.js                     app.py
                                       ├─ book_depository/isbn.py      (validate)
                                       └─ book_depository/metadata.py  (fetch+parse)
```

The contract between the two halves is one normalized `Book` object (see
`metadata.py`). Nothing downstream cares which API answered.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py          # http://localhost:8000
```

`localhost` is a secure context, so the camera works on your laptop. To test on a
phone you need HTTPS — deploy, or run a tunnel (e.g. `cloudflared tunnel --url
http://localhost:8000`).

## What's done vs. what's yours to write

Provided & working: camera + decode loop, Flask routing, the lookup seam, the
Book shape and source-fallback orchestration.

Your exercises (marked `TODO` in the code):
1. `isbn.py` → real ISBN-13 validation (prefix + checksum).
2. `metadata.py` → `_from_open_library` and `_from_google_books` parsers.
3. `scan.js` → smarter scan debouncing.

## Next slices (not built yet)

- **Persistence** — a `db.py` with SQLite (one file per owner). The lookup above is
  read-only; nothing is stored until the *register* flow.
- **Register / borrow / return** routes that write to the DB.
