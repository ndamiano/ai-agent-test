#!/usr/bin/env bash
# One-time-per-box host setup for the containerized Maestro deploy (run ON the prod box).
# Idempotent: safe to re-run. Installs Docker + compose plugin, fetches the Ren'Py SDK, and
# installs the Godot binary + matching export templates to the paths docker-compose bind-mounts.
#
# The GPU services (LM Studio, ComfyUI, TTS, Trellis) are NOT provisioned here — they run on the
# host separately and the container reaches them over host.docker.internal.
set -euo pipefail

# ── Versions / URLs — CONFIRM these match your box before trusting the defaults ────────────────
RENPY_VERSION="8.5.2"
RENPY_SDK_HOST="${RENPY_SDK_HOST:-$HOME/renpy-${RENPY_VERSION}-sdk}"
RENPY_URL="https://www.renpy.org/dl/${RENPY_VERSION}/renpy-${RENPY_VERSION}-sdk.tar.bz2"

# Pinned to the version the builds are validated against (dev box runs 4.7.stable). The export
# templates MUST match the binary version exactly or native export fails.
GODOT_VERSION="${GODOT_VERSION:-4.7-stable}"
GODOT_BIN_HOST="${GODOT_BIN_HOST:-/usr/local/bin/godot}"
GODOT_TEMPLATES_HOST="${GODOT_TEMPLATES_HOST:-$HOME/.local/share/godot/export_templates}"
GODOT_BIN_URL="https://github.com/godotengine/godot/releases/download/${GODOT_VERSION}/Godot_v${GODOT_VERSION}_linux.x86_64.zip"
GODOT_TEMPLATES_URL="https://github.com/godotengine/godot/releases/download/${GODOT_VERSION}/Godot_v${GODOT_VERSION}_export_templates.tpz"

log() { echo -e "\n\033[1;36m==> $*\033[0m"; }

# ── Fetch + extract prerequisites ────────────────────────────────────────────────────────────
# A minimal Ubuntu image ships without these: bzip2 (Ren'Py SDK is .tar.bz2) and unzip (Godot
# binary/templates). Without them the extracts below fail. Idempotent — a no-op once present.
log "Installing prerequisites (curl, bzip2, unzip)"
sudo apt-get update
sudo apt-get install -y curl ca-certificates bzip2 unzip

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

# ── Ren'Py SDK ──────────────────────────────────────────────────────────────────────────────────
if [ -x "${RENPY_SDK_HOST}/renpy.sh" ]; then
  log "Ren'Py SDK already present at ${RENPY_SDK_HOST}"
else
  log "Fetching Ren'Py ${RENPY_VERSION} SDK -> ${RENPY_SDK_HOST}"
  tmp="$(mktemp -d)"
  curl -fSL "${RENPY_URL}" -o "${tmp}/renpy-sdk.tar.bz2"
  mkdir -p "$(dirname "${RENPY_SDK_HOST}")"
  tar -xjf "${tmp}/renpy-sdk.tar.bz2" -C "$(dirname "${RENPY_SDK_HOST}")"
  rm -rf "${tmp}"
  # The tarball extracts to renpy-<ver>-sdk/ already; verify the launcher landed.
  [ -x "${RENPY_SDK_HOST}/renpy.sh" ] || echo "WARN: renpy.sh not found under ${RENPY_SDK_HOST} — check the extract path."
fi

# ── Godot binary ────────────────────────────────────────────────────────────────────────────────
if command -v godot >/dev/null 2>&1 || [ -x "${GODOT_BIN_HOST}" ]; then
  log "Godot binary already present (${GODOT_BIN_HOST} or on PATH)"
else
  log "Fetching Godot ${GODOT_VERSION} -> ${GODOT_BIN_HOST}"
  tmp="$(mktemp -d)"
  curl -fSL "${GODOT_BIN_URL}" -o "${tmp}/godot.zip"
  unzip -o "${tmp}/godot.zip" -d "${tmp}"
  bin="$(find "${tmp}" -name 'Godot_v*_linux.x86_64' | head -n1)"
  sudo install -m 0755 "${bin}" "${GODOT_BIN_HOST}"
  rm -rf "${tmp}"
fi

# ── Godot export templates (version-matched) ─────────────────────────────────────────────────────
TEMPLATE_DEST="${GODOT_TEMPLATES_HOST}/${GODOT_VERSION/-/.}"   # e.g. 4.3-stable -> 4.3.stable
if [ -d "${TEMPLATE_DEST}" ] && [ -n "$(ls -A "${TEMPLATE_DEST}" 2>/dev/null)" ]; then
  log "Godot export templates already present at ${TEMPLATE_DEST}"
else
  log "Fetching Godot ${GODOT_VERSION} export templates -> ${TEMPLATE_DEST}"
  tmp="$(mktemp -d)"
  curl -fSL "${GODOT_TEMPLATES_URL}" -o "${tmp}/templates.tpz"
  # A .tpz is a zip whose members live under templates/; flatten into the versioned dir.
  unzip -o "${tmp}/templates.tpz" -d "${tmp}"
  mkdir -p "${TEMPLATE_DEST}"
  cp -a "${tmp}/templates/." "${TEMPLATE_DEST}/"
  rm -rf "${tmp}"
fi

log "Provisioning complete."
cat <<EOF

Next steps:
  1. Put the app source on this box (usually via scripts/deploy.sh from the dev box).
  2. In the app dir:  cp .env.example .env  &&  edit .env
       - set RENPY_SDK_HOST=${RENPY_SDK_HOST}
       - set GODOT_BIN_HOST=${GODOT_BIN_HOST}
       - set GODOT_TEMPLATES_HOST=${GODOT_TEMPLATES_HOST}
       - point COMFYUI_ENDPOINT at the host GPU services (the LLM has no URL here — the
         worker's --target names its server).
  3. docker compose build && docker compose up -d
  4. Create an account:  docker compose exec app python -m auth.cli create <handle>
EOF
