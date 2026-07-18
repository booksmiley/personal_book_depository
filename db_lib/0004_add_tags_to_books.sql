-- Hand-curated subject tags, comma-separated ("systematic theology, luther");
-- the first tag is the primary category. Deliberately NOT set by the online
-- metadata lookup — BOOK_INSERT leaves it NULL and tags are filled in later by
-- hand (or by Claude) following docs/tagging.md.
ALTER TABLE books ADD COLUMN tags TEXT;
