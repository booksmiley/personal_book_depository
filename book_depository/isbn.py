"""ISBN-13 helpers — pure functions, no I/O. The easiest module to unit-test.

A printed book barcode is an EAN-13, and for books that number IS the ISBN-13
(the ones starting 978 / 979). So decoding the barcode hands you the ISBN directly;
there's no separate parse step.
"""

import re


def normalize_isbn(raw: str) -> str:
    """Strip everything that isn't a digit (dashes, spaces, stray characters)."""
    return re.sub(r"[^0-9]", "", raw or "")


def is_valid_isbn13(isbn: str) -> bool:
    """Return True only for a well-formed book ISBN-13.

    NOTE: This is a placeholder so the pipeline runs end-to-end right now.
    It only checks "13 digits" — which lets misreads through.

    TODO (your exercise): make it a real check —
      1. exactly 13 digits                          -> re.fullmatch(r"\\d{13}", isbn)
      2. book prefix: starts with "978" or "979"
      3. checksum: multiply digits by alternating weights 1,3,1,3,...,1
         and the total must be divisible by 10.
         e.g. for digits d0..d12:  sum(d[i] * (1 if i % 2 == 0 else 3)) % 10 == 0
    Then write a couple of asserts at the bottom (real ISBN passes, tweaked one fails)
    and run `python -m book_depository.isbn` to check yourself.
    """
    return bool(re.fullmatch(r"\d{13}", isbn or ""))


if __name__ == "__main__":
    # Scratch space for self-testing while you implement the checksum.
    # TODO: add assertions, e.g.
    #   assert is_valid_isbn13("9780131103627")      # real ISBN -> True
    #   assert not is_valid_isbn13("9780131103628")  # bad checksum -> False
    print("isbn.py loaded — add your assertions here.")
