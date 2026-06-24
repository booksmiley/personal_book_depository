# TODO

## Data safety: back up the local SQLite DB to iCloud
Keep the live DB local (`~/.book_depository/data`) but write consistent snapshots
into iCloud for off-machine safety and cross-device restore.

**Do NOT put the live `.sqlite` in iCloud directly** — sync corrupts it (WAL/-shm
sidecars synced partially, mid-write grabs, placeholder eviction).

Safe approach:
- Add `backup_dir` to `local_config/config.yml` (e.g.
  `~/Library/Mobile Documents/com~apple~CloudDocs/book_depository/`; blank = off).
- `backup_db(conn, backup_path)` in `db.py` using the SQLite **online backup API**
  (`conn.backup(dest)` → consistent single-file snapshot, no sidecars), written to a
  temp file then **atomically `os.replace`** into `backup_dir` (so iCloud only ever
  sees a complete file).
- Call it after each successful write (register / borrow / return). Writes are rare,
  so per-write is fine and keeps the snapshot current.
- Snapshots are for safety/restore, not live multi-device editing — still write from
  one machine only.

## Next feature: borrow / return flows
- Python: insert `status` ledger event, decrement/increment `available`, guard
  `available > 0` (borrow) and `available <= total_count` (return), FIFO loan match.
- Frontend (Claude): borrow/return UI on the scanned-book card.
