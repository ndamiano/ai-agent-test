#!/usr/bin/env bash
# Start llama.cpp in router mode, wait for it, then hand the container over to the worker agent.
#
# llama-server binds 127.0.0.1: nothing about this pod is reachable from outside. The worker dials
# OUT to the control plane, so the pod needs no exposed port and no inbound rule. An llm-server
# reachable from the internet is an unauthenticated GPU.
set -euo pipefail

: "${CP_URL:?CP_URL (control plane base URL) is required}"
: "${WORKER_TOKEN:?WORKER_TOKEN is required}"

# Router mode: children inherit these, so they match the home box's serving config exactly.
/app/llama-server \
    --models-dir "$MODELS_DIR" \
    --host 127.0.0.1 --port "$LLAMA_PORT" \
    -ngl 99 -c 32768 --jinja --reasoning-budget 0 &
llama_pid=$!

# The router answers /models before any weights load (models load on first request), so this waits
# for the process, not for the 22 GB pull off the network volume.
for _ in $(seq 1 60); do
    if curl -sf "http://127.0.0.1:$LLAMA_PORT/models" >/dev/null; then break; fi
    kill -0 "$llama_pid" 2>/dev/null || { echo "llama-server died during startup" >&2; exit 1; }
    sleep 2
done
curl -sf "http://127.0.0.1:$LLAMA_PORT/models" >/dev/null || {
    echo "llama-server did not answer /models within 120s" >&2; exit 1; }
echo "llama-server up; models: $(curl -s "http://127.0.0.1:$LLAMA_PORT/models" | head -c 400)"

exec python3 -m worker.agent \
    --server "$CP_URL" \
    --queue llm \
    --target "http://127.0.0.1:$LLAMA_PORT" \
    --source runpod \
    ${GPU_TYPE:+--gpu-type "$GPU_TYPE"}
