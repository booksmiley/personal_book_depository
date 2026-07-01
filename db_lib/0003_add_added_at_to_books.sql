-- Per-book "added to the library" timestamp. Existing rows are backfilled with the
-- moment this migration first runs; new rows get their real insert time from
-- BOOK_INSERT (which sets added_at = datetime('now')).
ALTER TABLE books ADD COLUMN added_at TEXT;
UPDATE books SET added_at = datetime('now') WHERE added_at IS NULL;
