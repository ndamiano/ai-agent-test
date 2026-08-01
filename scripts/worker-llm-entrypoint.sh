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
# --chat-template-kwargs: --reasoning-budget 0 is a NO-OP for Qwen3.6's template — the model
# thinks in content and truncates at the output cap before ever emitting the message/tool call
# (measured: whole authoring turns lost as 16K-token reasoning blobs). enable_thinking=false
# switches the template itself; verified locally: zero reasoning tokens, clean tool calls.
#
# -c must match LLM_N_CTX on the control plane: it is what the input budget is computed from, and a
# window larger than the server's is one that never trims until the prompt has already overflowed.
# llama.cpp preallocates ALL of it at load, so the KV quant is not an optimisation here but what
# decides whether the model loads at all: the dense 27B keeps 64 layers × 4 kv heads × 256, which is
# 136 KiB/token at q8_0 — 17.0 GiB for this window, over a 32 GiB card once the weights are in.
# q4_0 halves it. -fa is required for the quantized cache to be used.
/app/llama-server \
    --models-dir "$MODELS_DIR" \
    --host 127.0.0.1 --port "$LLAMA_PORT" \
    -ngl 99 -c 131072 -fa on \
    --cache-type-k q4_0 --cache-type-v q4_0 \
    --jinja --reasoning-budget 0 \
    --chat-template-kwargs '{"enable_thinking":false}' &
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

# No `exec`: RunPod restarts an exited container and keeps billing (even exit 0), so a clean
# agent exit must be followed by an API pod kill. Best-effort here (fires only when the pod env
# carries RUNPOD_API_KEY — RunPod injects no key on its own); the
# control-plane reaper is the billing guarantee. Nonzero exits pass through — RunPod's restart
# is free crash recovery.
python3 -m worker.agent \
    --server "$CP_URL" \
    --queue llm \
    --target "http://127.0.0.1:$LLAMA_PORT" \
    --source runpod \
    ${GPU_TYPE:+--gpu-type "$GPU_TYPE"} \
    ${IDLE_EXIT_SECONDS:+--idle-exit-seconds "$IDLE_EXIT_SECONDS"} &
agent_pid=$!

trap 'kill -TERM "$agent_pid" 2>/dev/null || true' TERM INT
set +e
wait "$agent_pid"; rc=$?
# A trapped signal interrupts wait before the agent is reaped — wait again for the real status.
if [ "$rc" -gt 128 ]; then wait "$agent_pid"; rc=$?; fi
set -e

if [ "$rc" -eq 0 ] && [ -n "${RUNPOD_POD_ID:-}" ]; then
    if [ -z "${RUNPOD_API_KEY:-}" ]; then
        echo "self-terminate skipped: RUNPOD_API_KEY not set — the reaper must collect this pod" >&2
    else
        echo "clean exit — self-terminating pod $RUNPOD_POD_ID"
        for _ in 1 2 3; do
            code=$(curl -s -o /tmp/selfterm.out -w '%{http_code}' -X DELETE \
                "https://rest.runpod.io/v1/pods/$RUNPOD_POD_ID" \
                -H "Authorization: Bearer $RUNPOD_API_KEY")
            case "$code" in
                2*|404) echo "self-terminate accepted (HTTP $code)"; break ;;
            esac
            echo "self-terminate failed: HTTP $code $(head -c 200 /tmp/selfterm.out)" >&2
            sleep 2
        done
    fi
fi
exit "$rc"
