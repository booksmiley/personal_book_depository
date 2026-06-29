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
        s += d * 1 if idx % 2 == 0 else d * 3
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

    if isbn[:3] not in ("978", "979"):
        return False

    if not isbn_check_sum(isbn):
        return False

    return True


# ── ISBN-10 support ───────────────────────────────────────────────────────────
# Old books carry a 10-digit ISBN. Convert it to its 978-prefixed ISBN-13 at the
# front door so register/borrow/return stay keyed on ISBN-13 with no schema change.

def _clean_isbn(raw: str) -> str:
    """Uppercase; keep only digits and a trailing X (the ISBN-10 check char).
    Unlike normalize_isbn(), this preserves 'X', so ISBN-10s ending in X
    (e.g. 080442957X) survive. Don't route ISBN-10 through normalize_isbn()."""
    return re.sub(r"[^0-9X]", "", (raw or "").upper())


def is_valid_isbn10(isbn: str) -> bool:
    """9 digits + a check char (0-9 or X), weighted 10..1, summing to a
    multiple of 11."""
    if not re.fullmatch(r"\d{9}[\dX]", isbn or ""):
        return False
    total = sum((10 if c == "X" else int(c)) * (10 - i) for i, c in enumerate(isbn))
    return total % 11 == 0


def isbn10_to_isbn13(isbn10: str) -> str:
    """Drop the ISBN-10 check digit, prepend 978, recompute the ISBN-13 check."""
    core = "978" + isbn10[:9]
    chk = (10 - sum((1 if i % 2 == 0 else 3) * int(d)
                    for i, d in enumerate(core)) % 10) % 10
    return core + str(chk)


def to_isbn13(raw: str):
    """Coerce ISBN-10 OR ISBN-13 input (dashes/spaces/X allowed) to a canonical
    13-digit ISBN-13 string, or None if neither form is valid. The single front
    door the routes call."""
    s = _clean_isbn(raw)
    if len(s) == 13:
        return s if is_valid_isbn13(s) else None
    if len(s) == 10:
        return isbn10_to_isbn13(s) if is_valid_isbn10(s) else None
    return None


if __name__ == "__main__":
    assert is_valid_isbn13("9780131103627")  # real ISBN -> expect True
    assert not is_valid_isbn13("9780131103628")  # bad check digit -> expect False
    assert not is_valid_isbn13("1234567890123")  # bad prefix -> expect False
    assert not is_valid_isbn13("978013110362")  # only 12 digits -> expect False
    assert not is_valid_isbn13("978013110362X")  # has a letter -> expect False

    # ISBN-10 validation + conversion
    assert is_valid_isbn10("0131103628")  # real ISBN-10 -> True
    assert is_valid_isbn10("080442957X")  # valid X check digit -> True
    assert not is_valid_isbn10("0131103629")  # bad check digit -> False
    assert not is_valid_isbn10("013110362")  # only 9 chars -> False
    assert isbn10_to_isbn13("0131103628") == "9780131103627"  # known pair

    # to_isbn13 front door: accepts both forms, dashes, spaces, lowercase x
    assert to_isbn13("0-13-110362-8") == "9780131103627"  # dashed ISBN-10
    assert to_isbn13("978-0-13-110362-7") == "9780131103627"  # dashed ISBN-13
    assert to_isbn13("080442957x") == "9780804429573"  # lowercase x
    assert to_isbn13("0131103629") is None  # bad ISBN-10 checksum
    assert to_isbn13("9780131103628") is None  # bad ISBN-13 checksum
    assert to_isbn13("978013110362") is None  # wrong length
    print("All ISBN checks passed ✅")
