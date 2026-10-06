#!/usr/bin/env bash
# Daily backup of the live SQLite database, meant to run from the systemd
# timer on the droplet (deploy/systemd/pool-league-backup.*). Uses sqlite3's
# own `.backup` command rather than `cp`: copying a database file while the
# app is mid-write can produce a backup missing whatever was still in the
# journal/WAL. `.backup` takes a proper read lock and gives a consistent
# snapshot instead.
#
# Usage: scripts/backup.sh [db_path] [backup_dir]
#   db_path     defaults to ./data/league.db
#   backup_dir  defaults to ./data/backups
#
# Env:
#   REASON      label embedded in the filename (default "nightly"); deploy.sh
#               sets "deploy" or "rollback" so those backups are identifiable
#               and never overwrite each other or the nightly one.
#
# Files are named league-<YYYY-MM-DD-HHMMSS>-<reason>.db. Keeps the 14 newest
# league-*.db files (by mtime) and deletes the rest: roughly two weeks of
# "oh no" coverage at one a day, fewer days if deploys add extras. Backups live on the same host as the database, so this
# protects against bad edits and bad migrations, not against losing the
# droplet (that's what DigitalOcean snapshots are for).
set -euo pipefail

DB_PATH="${1:-./data/league.db}"
BACKUP_DIR="${2:-./data/backups}"
KEEP=14
REASON="${REASON:-nightly}"

if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "backup.sh: sqlite3 not found. On the droplet: apt install sqlite3" >&2
    exit 1
fi

if [ ! -f "$DB_PATH" ]; then
    echo "backup.sh: no database at $DB_PATH, nothing to back up" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

STAMP="$(date +%Y-%m-%d-%H%M%S)"
DEST="$BACKUP_DIR/league-$STAMP-$REASON.db"
# Two runs with the same reason inside one second would collide; never overwrite.
N=1
while [ -e "$DEST" ]; do
    DEST="$BACKUP_DIR/league-$STAMP-$REASON-$N.db"
    N=$((N + 1))
done

# .backup is online-safe, unlike `cp`, which copies whatever bytes happen
# to be on disk at that instant.
sqlite3 "$DB_PATH" ".backup '$DEST'"

# A backup that doesn't open is worse than no backup: it's a false sense of
# safety. Cheap to check now, expensive to find out at restore time.
INTEGRITY="$(sqlite3 "$DEST" "pragma integrity_check;")"
if [ "$INTEGRITY" != "ok" ]; then
    echo "backup.sh: integrity check failed for $DEST: $INTEGRITY" >&2
    exit 1
fi

echo "backup.sh: wrote $DEST (integrity check: ok)"

# Newest-first by mtime; skip the first KEEP and remove the rest. The glob
# always matches at least the file just written, so ls can't fail on an empty
# match, and `xargs -r` skips rm when there is nothing to delete.
ls -1t -- "$BACKUP_DIR"/league-*.db \
    | tail -n "+$((KEEP + 1))" \
    | xargs -r rm -f --

REMAINING="$(find "$BACKUP_DIR" -maxdepth 1 -name 'league-*.db' | wc -l | tr -d ' ')"
echo "backup.sh: $REMAINING backup(s) retained in $BACKUP_DIR"
