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

# No `exec`: RunPod restarts an exited container and keeps billing (even exit 0), so a clean
# agent exit must be followed by an API pod kill. Best-effort here (fires only when the pod env
# carries RUNPOD_API_KEY — RunPod injects no key on its own); the
# control-plane reaper is the billing guarantee. Nonzero exits pass through — RunPod's restart
# is free crash recovery.
python -m worker.agent \
    --server "$CP_URL" \
    --queue image \
    --target "http://127.0.0.1:$COMFY_PORT" \
    --source runpod \
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
