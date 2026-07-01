"""Individual metadata source modules (per-provider scrapers/clients).

Each module exposes a `fetch_*_metadata(isbn)` function returning a plain dict (or
None). `book_depository.metadata` imports these and combines them into one `Book`.
"""
