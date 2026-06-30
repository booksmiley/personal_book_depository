#!/bin/sh
# Container entrypoint: restore the DB from object storage, then run the app under
# Litestream so every change is streamed back up.
set -e

DB=/data/lib_admin.sqlite

# 1) Restore from the bucket only if we don't already have the file locally.
#    -if-replica-exists makes the very first boot (empty bucket) a no-op instead of
#    an error; the app then creates a fresh DB and migrations bring it up to date.
if [ ! -f "$DB" ]; then
  echo "No local DB found — attempting restore from replica…"
  litestream restore -if-replica-exists -config /etc/litestream.yml "$DB"
fi

# 2) Replicate continuously while running gunicorn as a child process.
#    --workers 1 keeps a SINGLE process (one Litestream writer, no WAL-checkpoint
#    fights). --threads handles requests concurrently within that process: while one
#    thread is blocked on a slow metadata lookup (network I/O releases the GIL),
#    other threads can serve borrow/return. Each request uses its own SQLite
#    connection; WAL + busy_timeout serialise the writes safely.
exec litestream replicate -config /etc/litestream.yml \
  -exec "gunicorn app:app --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 8"
