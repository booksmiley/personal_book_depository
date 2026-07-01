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

# 2) Replicate continuously while running the app (hypercorn, ASGI) as a child
#    process. A single async worker serves requests concurrently on one event loop:
#    while one request awaits a slow metadata lookup, others (borrow/return) keep
#    running. One process keeps Litestream's single-writer assumption intact.
exec litestream replicate -config /etc/litestream.yml \
  -exec "hypercorn app:app --bind 0.0.0.0:${PORT:-8000} --workers 1"
