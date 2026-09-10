#!/usr/bin/env bash
# Start the TRELLIS server, wait for it, run the worker agent, and on a CLEAN agent exit
# best-effort self-terminate the pod.
#
# No `exec`: RunPod RESTARTS an exited container and keeps billing (even exit 0), so the agent's
# self-exit is only the *decision* — the pod must be killed by API. The in-pod DELETE fires only
# when the env carries RUNPOD_API_KEY (RunPod injects no key on its own); the control-plane
# reaper is the billing guarantee.
# A NONZERO exit passes through without self-terminate: RunPod's restart is free crash recovery.
#
# The TRELLIS server binds 127.0.0.1 and no port is exposed: a reachable inference server is an
# unauthenticated GPU. The worker dials OUT to the control plane.
set -euo pipefail

: "${CP_URL:?CP_URL (control plane base URL) is required}"
: "${WORKER_TOKEN:?WORKER_TOKEN is required}"

test -d "$TRELLIS_WEIGHTS" || {
    echo "network volume not mounted: $TRELLIS_WEIGHTS is missing" >&2; exit 1; }

# Volume throughput probe: one sequential GiB off the biggest ckpt. Cold-start diagnosis — if
# this rate ≈ the pipeline load rate, the volume is the bottleneck and pre-copying to local disk
# buys nothing; if it's much faster, the loader's read pattern (mmap page faults) is the problem.
probe=$(ls -S "$TRELLIS_WEIGHTS"/ckpts/*.safetensors 2>/dev/null | head -1)
if [ -n "$probe" ]; then
    dd if="$probe" of=/dev/null bs=64M count=16 2>&1 | tail -1 | sed 's/^/[probe] volume seq read: /'
fi

# The sheet server shares this pod: a sheet is always rendered from a mesh made here, so the glb
# stays on the box. Both bind loopback — a reachable inference server is an unauthenticated GPU.
if [ -n "${SPRITE_PYTHON:-}" ]; then
    "$SPRITE_PYTHON" /opt/maestro/src/tools/sprite_server.py \
        --host 127.0.0.1 --port "${SPRITE_PORT:-8190}" \
        ${KIMODO_TPOSE_BVH:+--ref-bvh "$KIMODO_TPOSE_BVH"} &
    sprite_pid=$!
    trap 'kill -TERM "$sprite_pid" 2>/dev/null || true' EXIT
fi

python /opt/maestro/src/tools/trellis_server.py \
    --repo "$TRELLIS_REPO" --weights "$TRELLIS_WEIGHTS" \
    --host 127.0.0.1 --port "$TRELLIS_PORT" &
trellis_pid=$!

# The server stages its checkpoints, loads the pipeline and runs one throwaway mesh in the
# background, so /health answers as soon as uvicorn binds and reports readiness separately. Wait
# for WARM, not the bind: a worker that registers first claims a job that then eats the whole
# cold start — the load, the lazy encoders and the first-use kernel compile (53s vs 13s,
# measured) — wall-clock the same, but debited to the user's grant as if it were work. Nothing
# can generate before that finishes, so waiting costs nothing and a pod that cannot generate at
# all is caught here rather than by failing a claimed job.
for _ in $(seq 1 150); do
    curl -sf "http://127.0.0.1:$TRELLIS_PORT/health" 2>/dev/null | grep -q '"warm": *true' && break
    kill -0 "$trellis_pid" 2>/dev/null || { echo "trellis server died during startup" >&2; exit 1; }
    sleep 2
done
curl -sf "http://127.0.0.1:$TRELLIS_PORT/health" >/dev/null || {
    echo "trellis server did not answer /health within 300s" >&2; exit 1; }
# Past the deadline but alive: serve anyway rather than burn the pod — the first job pays.
echo "trellis up: $(curl -s "http://127.0.0.1:$TRELLIS_PORT/health")"

python -m worker.agent \
    --server "$CP_URL" \
    --queue mesh \
    --target "http://127.0.0.1:$TRELLIS_PORT" \
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
