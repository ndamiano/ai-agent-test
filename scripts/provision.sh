#!/usr/bin/env bash
# One-time-per-box host setup for the containerized Maestro deploy (run ON the prod box).
# Idempotent: safe to re-run. Installs Docker + the compose plugin.
set -euo pipefail

log() { echo -e "\n\033[1;36m==> $*\033[0m"; }

log "Installing prerequisites (curl)"
sudo apt-get update
sudo apt-get install -y curl ca-certificates

# ── Docker + compose plugin (Debian/Ubuntu) ────────────────────────────────────────────────────
if command -v docker >/dev/null 2>&1; then
  log "Docker already installed: $(docker --version)"
else
  log "Installing Docker Engine + compose plugin"
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl gnupg
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
    sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  # shellcheck disable=SC1091
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo usermod -aG docker "$USER" || true
  echo "NOTE: log out/in (or run 'newgrp docker') so your user can run docker without sudo."
fi

log "Provisioning complete."
cat <<EOF

Next steps:
  1. Put the app source on this box (usually via scripts/deploy.sh from the dev box).
  2. In the app dir:  cp .env.example .env  &&  edit .env  (WORKQUEUE_TOKEN is the one required
     value — the GPUs are reached only by worker agents, which name their own --target.)
  3. docker compose build && docker compose up -d
  4. Create an account:  docker compose exec app python -m auth.cli create <handle> <email>
EOF
