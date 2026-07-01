"""Unit tests for the pure ISBN helpers."""

from book_depository import isbn


def test_to_isbn13_accepts_both_forms_and_separators():
    assert isbn.to_isbn13("0-13-110362-8") == "9780131103627"       # dashed ISBN-10
    assert isbn.to_isbn13("978-0-13-110362-7") == "9780131103627"   # dashed ISBN-13
    assert isbn.to_isbn13("080442957x") == "9780804429573"          # lowercase X


def test_to_isbn13_rejects_bad_input():
    assert isbn.to_isbn13("9780131103628") is None  # bad ISBN-13 checksum
    assert isbn.to_isbn13("0131103629") is None     # bad ISBN-10 checksum
    assert isbn.to_isbn13("978013110362") is None   # wrong length
    assert isbn.to_isbn13("not-an-isbn") is None


def test_isbn10_13_roundtrip():
    assert isbn.isbn10_to_isbn13("0131103628") == "9780131103627"
    assert isbn.isbn13_to_isbn10("9780131103627") == "0131103628"
    assert isbn.isbn13_to_isbn10("9791234567896") == ""  # 979 has no ISBN-10 form


def test_chinese_or_us_gate():
    assert isbn.is_chinese_or_us_isbn("9787802546189")   # mainland China (978-7)
    assert isbn.is_chinese_or_us_isbn("9781932184600")   # US-published Chinese (978-1)
    assert isbn.is_chinese_or_us_isbn("9789575876241")   # Taiwan (957)
    assert not isbn.is_chinese_or_us_isbn("9780131103627")  # 978-0 English
    assert not isbn.is_chinese_or_us_isbn("978193218460")   # not 13 digits
