# syntax=docker/dockerfile:1

# Stage 1 — build the frontend SPA into frontend/dist (served same-origin by the backend).
FROM node:20-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


# Stage 2 — the mesh toolchain (gltf-transform + meshoptimizer, for runtime/decimate.mjs),
# resolved for linux in a node stage so the platform-specific native binaries land correctly.
FROM node:20-slim AS mesh-toolchain
WORKDIR /toolchain
COPY runtime/package.json runtime/package-lock.json ./
RUN npm ci


# Stage 3 — CPU-only Python runtime. The container is the CONTROL PLANE: API + SPA + the job
# queue. GPU inference happens on worker agents that PULL jobs over /worker — nothing GPU-shaped
# lives here. The one subprocess is node, decimating a finished TRELLIS GLB to game weight.
FROM python:3.12-slim AS runtime

# curl for the compose healthcheck; git for a run's game history (maestro.codegen.snapshots);
# node for runtime/decimate.mjs.
RUN apt-get update && apt-get install -y --no-install-recommends curl git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=frontend /usr/local/bin/node /usr/local/bin/node

RUN useradd --create-home --home-dir /home/maestro --shell /bin/bash maestro
ENV HOME=/home/maestro

WORKDIR /app

# Layer-cached before the source copy so a code change doesn't reinstall deps.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Preserve the layout app.py's static-mount math depends on:
# Path(__file__).resolve().parents[2] from /app/src/api/app.py == /app, so dist lands at
# /app/frontend/dist, runtime/ at /app/runtime, and run.py inserts /app/src on sys.path.
# runtime/ carries the vendored three.js that every game folder is seeded from.
COPY run.py ./
COPY src/ ./src/
COPY runtime/ ./runtime/
COPY --from=mesh-toolchain /toolchain/node_modules ./runtime/node_modules
COPY --from=frontend /build/frontend/dist ./frontend/dist

# Durable state lives OUTSIDE the source tree on named volumes: /data (runs/ + private/ dbs) and
# /app/runtime/games (staged playable bundles — served at /play, must survive image rebuilds).
# Creating + chowning the mountpoints means the empty named volumes inherit maestro's ownership
# on first mount, so the non-root process can write without an entrypoint chown.
RUN mkdir -p /data /app/runtime/games && chown -R maestro:maestro /data /app
ENV WORKING_DIRECTORY=/data

USER maestro

EXPOSE 8000

# run.py reads HOST/PORT/MAESTRO_DEV from env; MAESTRO_DEV unset == production (reload off).
CMD ["python", "run.py"]
