#!/usr/bin/env bash
# Nightly restic backup of the runs/ tree (run dirs: spec, cursor, turn log, game source +
# game.git history) to the same S3 target Litestream replicates the DBs to. Runs on the HOST
# (cron/systemd timer — docs/backups.md has the line), not in a container: the runs live inside
# the maestro-data volume, which the host can read directly.
#
# Required env (put them in /etc/maestro-backup.env, root-owned 0600, and source before running):
#   RESTIC_REPOSITORY      e.g. s3:https://<endpoint>/<bucket>/restic
#   RESTIC_PASSWORD        (or RESTIC_PASSWORD_FILE) — encrypts the repo; losing it loses the backup
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
# Optional:
#   RUNS_DIR               override the path to back up (default: <maestro-data mountpoint>/runs)
set -euo pipefail

: "${RESTIC_REPOSITORY:?set RESTIC_REPOSITORY (s3:https://<endpoint>/<bucket>/restic)}"
if [ -z "${RESTIC_PASSWORD:-}" ] && [ -z "${RESTIC_PASSWORD_FILE:-}" ]; then
  echo "ERROR: set RESTIC_PASSWORD or RESTIC_PASSWORD_FILE" >&2
  exit 1
fi

RUNS_DIR="${RUNS_DIR:-$(docker volume inspect maestro-data -f '{{ .Mountpoint }}')/runs}"
if [ ! -d "${RUNS_DIR}" ]; then
  echo "ERROR: runs dir not found at ${RUNS_DIR}" >&2
  exit 1
fi

restic cat config >/dev/null 2>&1 || restic init

restic backup "${RUNS_DIR}" --tag maestro-runs
restic forget --tag maestro-runs --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune
