#!/usr/bin/env bash
# Ship the app from the DEV box to the REMOTE prod box, then rebuild + restart the container there.
#
#   PROD_HOST=user@prod-box ./scripts/deploy.sh
#   ./scripts/deploy.sh user@prod-box           # positional target also works
#
# The durable data lives in a Docker NAMED VOLUME on the prod box (outside the source tree), so
# rsync never touches it — the excludes below are belt-and-suspenders. Fails loudly on any error.
set -euo pipefail

PROD_HOST="${1:-${PROD_HOST:-}}"
if [ -z "${PROD_HOST}" ]; then
  echo "ERROR: set PROD_HOST (env var or first arg), e.g. PROD_HOST=user@host ./scripts/deploy.sh" >&2
  exit 1
fi

# Remote dir the app source lives in on the prod box.
REMOTE_DIR="${REMOTE_DIR:-/opt/maestro}"
# Port to probe for the health check (matches PORT in the prod .env).
HEALTH_PORT="${HEALTH_PORT:-8000}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Syncing source to ${PROD_HOST}:${REMOTE_DIR}"
ssh "${PROD_HOST}" "mkdir -p '${REMOTE_DIR}'"
rsync -az --delete \
  --exclude '.git/' \
  --exclude 'venv/' \
  --exclude 'frontend/node_modules/' \
  --exclude 'frontend/dist/' \
  --exclude 'runtime/node_modules/' \
  --exclude 'runtime/games/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'src/logs/' \
  --exclude 'tasks/nicknotes.md' \
  --exclude 'outputs/' \
  --exclude '.env' \
  --exclude 'src/config/settings.json' \
  "${REPO_ROOT}/" "${PROD_HOST}:${REMOTE_DIR}/"

echo "==> Building + restarting the container on ${PROD_HOST}"
# .env must already exist on the prod box (created once from .env.example — deploy never ships it).
ssh "${PROD_HOST}" "cd '${REMOTE_DIR}' && test -f .env || { echo 'ERROR: ${REMOTE_DIR}/.env missing — run provision.sh + create .env first' >&2; exit 1; }"
ssh "${PROD_HOST}" "cd '${REMOTE_DIR}' && docker compose build && docker compose up -d"

echo "==> Health check"
# Probe from the prod box (localhost:PORT) so we don't depend on the app port being publicly open.
if ssh "${PROD_HOST}" "curl -fsS --retry 5 --retry-delay 3 --retry-connrefused http://localhost:${HEALTH_PORT}/healthz >/dev/null"; then
  echo "==> DEPLOY OK — ${PROD_HOST} is serving /healthz"
else
  echo "ERROR: health check failed on ${PROD_HOST}:${HEALTH_PORT}. Check 'docker compose logs' on the box." >&2
  exit 1
fi
