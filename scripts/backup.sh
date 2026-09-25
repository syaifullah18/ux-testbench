#!/usr/bin/env bash
# Nightly backup of everything that cannot be rebuilt from the repository:
#   - every SQLite file in DATA_DIR, copied with `sqlite3 .backup` so a live write is never
#     captured half-finished (a plain `cp` of a WAL database can be corrupt);
#   - DATA_DIR/projects, the YAML and uploaded prototypes edited through the Studio.
#
# Usage:  DATA_DIR=/srv/testbench/instance BACKUP_DIR=/srv/backups ./scripts/backup.sh
# Cron:   15 3 * * *  /srv/testbench/scripts/backup.sh >> /var/log/testbench-backup.log 2>&1
#
# Restore: stop the app, put the .db files back into DATA_DIR, untar projects/, start it again.
# Test a restore before you need one; an untested backup is a guess.
set -euo pipefail

DATA_DIR="${DATA_DIR:-./instance}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$BACKUP_DIR/$STAMP"

command -v sqlite3 >/dev/null || { echo "sqlite3 is required" >&2; exit 1; }
[ -d "$DATA_DIR" ] || { echo "DATA_DIR not found: $DATA_DIR" >&2; exit 1; }

mkdir -p "$DEST"
echo "→ Backing up $DATA_DIR to $DEST"

shopt -s nullglob
for db in "$DATA_DIR"/*.db; do
  name="$(basename "$db")"
  sqlite3 "$db" ".backup '$DEST/$name'"
  echo "  database $name"
done

if [ -d "$DATA_DIR/projects" ]; then
  tar -czf "$DEST/projects.tar.gz" -C "$DATA_DIR" projects
  echo "  projects.tar.gz"
fi

( cd "$DEST" && sha256sum * > SHA256SUMS 2>/dev/null || shasum -a 256 * > SHA256SUMS )

echo "→ Removing backups older than $KEEP_DAYS days"
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -mtime "+$KEEP_DAYS" -exec rm -rf {} +

echo "Done: $DEST"
