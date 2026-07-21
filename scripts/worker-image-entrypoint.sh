#!/usr/bin/env bash
# Start ComfyUI, wait for it, then hand the container over to the worker agent.
#
# ComfyUI binds 127.0.0.1 and no port is exposed: it has no auth of its own, so a reachable
# instance is an unauthenticated GPU. The worker dials OUT to the control plane.
set -euo pipefail

: "${CP_URL:?CP_URL (control plane base URL) is required}"
: "${WORKER_TOKEN:?WORKER_TOKEN is required}"

test -e /opt/ComfyUI/models/checkpoints || {
    echo "network volume not mounted: /workspace/comfy/models is missing" >&2; exit 1; }

python /opt/ComfyUI/main.py --listen 127.0.0.1 --port "$COMFY_PORT" --disable-auto-launch &
comfy_pid=$!

# ComfyUI imports torch and every custom node before it binds, so this waits on real startup work.
for _ in $(seq 1 90); do
    if curl -sf "http://127.0.0.1:$COMFY_PORT/system_stats" >/dev/null; then break; fi
    kill -0 "$comfy_pid" 2>/dev/null || { echo "ComfyUI died during startup" >&2; exit 1; }
    sleep 2
done
curl -sf "http://127.0.0.1:$COMFY_PORT/system_stats" >/dev/null || {
    echo "ComfyUI did not answer /system_stats within 180s" >&2; exit 1; }
echo "ComfyUI up: $(curl -s "http://127.0.0.1:$COMFY_PORT/system_stats" | head -c 300)"

# --llm-target "": no llama.cpp on this pod, so the handler's LLM eviction is a no-op it should
# skip rather than warn about. --comfy-target is this same instance — that IS the VRAM it frees.
exec python -m worker.agent \
    --server "$CP_URL" \
    --queue image \
    --target "http://127.0.0.1:$COMFY_PORT" \
    --comfy-target "http://127.0.0.1:$COMFY_PORT" \
    --llm-target "" \
    --source runpod \
    ${GPU_TYPE:+--gpu-type "$GPU_TYPE"}
