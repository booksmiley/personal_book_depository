"""ISBN-13 helpers — pure functions, no I/O. The easiest module to unit-test.

A printed book barcode is an EAN-13, and for books that number IS the ISBN-13
(the ones starting 978 / 979). So decoding the barcode hands you the ISBN directly;
there's no separate parse step.
"""

import re


def normalize_isbn(raw: str) -> str:
    """Strip everything that isn't a digit (dashes, spaces, stray characters)."""
    return re.sub(r"[^0-9]", "", raw or "")


def isbn_check_sum(isbn: str):
    s = 0
    for idx, d in enumerate(isbn):
        d = int(d)
        s += (d * 1 if idx % 2 == 0 else d * 3)
    return s % 10 == 0


def is_valid_isbn13(isbn: str) -> bool:
    """Return True only for a well-formed book ISBN-13.
    1. exactly 13 digits                          -> re.fullmatch(r"\\d{13}", isbn)
    2. book prefix: starts with "978" or "979"
    3. checksum: multiply digits by alternating weights 1,3,1,3,...,1
       and the total must be divisible by 10.
       e.g. for digits d0..d12:  sum(d[i] * (1 if i % 2 == 0 else 3)) % 10 == 0
    """
    if not bool(re.fullmatch(r"\d{13}", isbn or "")):
        return False

    if isbn[:3] not in ('978', '979'):
        return False

    if not isbn_check_sum(isbn):
        return False

    return True


if __name__ == "__main__":
    assert is_valid_isbn13("9780131103627")  # real ISBN -> expect True
    assert not is_valid_isbn13("9780131103628")  # bad check digit -> expect False
    assert not is_valid_isbn13("1234567890123")  # bad prefix -> expect False
    assert not is_valid_isbn13("978013110362")  # only 12 digits -> expect False
    assert not is_valid_isbn13("978013110362X")  # has a letter -> expect False
    print("All ISBN checks passed ✅")
