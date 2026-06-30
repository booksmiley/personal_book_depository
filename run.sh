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

# 2) Replicate continuously while running gunicorn as a child process. --workers 1
#    keeps a single writer so workers never fight over WAL checkpoints. When gunicorn
#    exits, Litestream flushes the final WAL frames before stopping.
exec litestream replicate -config /etc/litestream.yml \
  -exec "gunicorn app:app --bind 0.0.0.0:${PORT:-8000} --workers 1"
