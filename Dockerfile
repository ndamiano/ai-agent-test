# syntax=docker/dockerfile:1

# Stage 1 — build the frontend SPA into frontend/dist (served same-origin by the backend).
FROM node:20-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


# Stage 2 — CPU-only Python runtime. The app is a CLIENT of the GPU services (LM Studio, ComfyUI,
# TTS, Trellis) over HTTP; no CUDA here.
FROM python:3.12-slim AS runtime

# Shared libraries the HOST-MOUNTED engine binaries (renpy.sh, godot) need to EXECUTE headlessly.
# The SDK/Godot are not baked into the image (bind-mounted at runtime) but they still dlopen these.
# WHY apt here: the slim base has none of them; without these the mounted binaries fail to start.
# The exact set is to be validated on the first successful build — trim or extend as lint/export
# surface missing-.so errors.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        libgl1 \
        libglu1-mesa \
        libglib2.0-0 \
        fontconfig \
        libfreetype6 \
        libpng16-16 \
        libsdl2-2.0-0 \
        libasound2 \
        libx11-6 \
        libxext6 \
        libxrandr2 \
        libxcursor1 \
        libxinerama1 \
        libxi6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user with a real HOME so Godot resolves ~/.local/share/godot/export_templates.
RUN useradd --create-home --home-dir /home/maestro --shell /bin/bash maestro
ENV HOME=/home/maestro

WORKDIR /app

# Reference requirements.txt as-is (a parallel dependency audit rewrites it). Layer-cached before
# the source copy so a code change doesn't reinstall deps.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Preserve the layout app.py's static-mount math depends on:
# Path(__file__).resolve().parents[2] from /app/src/api/app.py == /app, so dist lands at
# /app/frontend/dist and run.py inserts /app/src on sys.path.
COPY run.py ./
COPY src/ ./src/
COPY --from=frontend /build/frontend/dist ./frontend/dist

# Durable state (runs/ + private/auth.db) lives OUTSIDE the source tree on a named volume.
# Creating + chowning the mountpoint means the empty named volume inherits maestro's ownership on
# first mount, so the non-root process can write without an entrypoint chown.
RUN mkdir -p /data && chown -R maestro:maestro /data /app
ENV WORKING_DIRECTORY=/data

USER maestro

EXPOSE 8000

# run.py reads HOST/PORT/MAESTRO_DEV from env; MAESTRO_DEV unset == production (reload off).
CMD ["python", "run.py"]
