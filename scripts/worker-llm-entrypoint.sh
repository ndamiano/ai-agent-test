#!/usr/bin/env bash
# Start Pennyroyal SGLang for Flash-Next, wait for health, hand the container to the worker agent.
# The engine binds 127.0.0.1 and the worker dials OUT to the control plane, so the pod needs no
# exposed port and no inbound rule: an llm server reachable from the internet is an
# unauthenticated GPU. A clean agent exit self-terminates the pod; the reaper is the billing
# guarantee.
set -euo pipefail

: "${CP_URL:?CP_URL (control plane base URL) is required}"
: "${WORKER_TOKEN:?WORKER_TOKEN is required}"
: "${LLM_MODEL:?LLM_MODEL is required — it is what the control plane names in every request}"
: "${LLM_N_CTX:?LLM_N_CTX is required — the control plane budgets its input against it}"

# The serve script bakes the served-model-name and the window; a request naming another model
# is refused, and a budget past the window is a prompt the engine rejects. Both are contracts
# the control plane's settings state, so a mismatch dies here rather than on the first turn.
SERVED_MODEL=pennyroyal
ENGINE_CONTEXT=524288
[ "$LLM_MODEL" = "$SERVED_MODEL" ] || {
    echo "LLM_MODEL=$LLM_MODEL but this engine serves $SERVED_MODEL" >&2; exit 1; }
[ "$LLM_N_CTX" -le "$ENGINE_CONTEXT" ] || {
    echo "LLM_N_CTX=$LLM_N_CTX exceeds the engine window $ENGINE_CONTEXT" >&2; exit 1; }

boot_t0=$(date +%s)
mark() { echo "[boot +$(( $(date +%s) - boot_t0 ))s] $*"; }

gpu=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || true)
mark "card: ${gpu:-unknown}"

# The engine (venv, fork checkout, kernel caches) is not in the image: it is one tarball on the
# volume, named by the hash the image was built against, so an image and a tarball from
# different builds can never meet. A pod restarted in place already has it.
env_id=$(cat /opt/maestro/env.id)
if [ ! -x /opt/venv/bin/python ]; then
    env_tar="$PENNY_ENV_DIR/llm-env-$env_id.tar"
    [ -f "$env_tar" ] || { echo "engine env $env_tar is not on the volume" >&2; exit 1; }
    mark "restoring engine env $env_id"
    tar -xf "$env_tar" -C /
    ldconfig
    mark "engine env restored"
fi

[ -f "$PENNY_MODEL_DIR/config.json" ] || { echo "no checkpoint at $PENNY_MODEL_DIR" >&2; exit 1; }

# The canonical Pennyroyal serve script (baked at /root/serve-nohicache.sh from the trial pod's
# cache tarball) is the ONLY launch that fits the card: it offloads the PLE table to host RAM
# (--ple-offload-embedding — without it weights alone exceed 96 GB), applies the YaRN rope
# override, and sets the qwen3 reasoning/tool-call parsers a build turn depends on. Tuning knobs
# ride $SGLANG_ARGS_EXTRA appended inside that script's env, not here.
export TARGET_MODEL="$PENNY_MODEL_DIR" CACHE_BASE=/root/cache NIXL_STORAGE_BASE=/root/nixl
export SGLANG_EXE=/opt/venv/bin/sglang REPO_ROOT=/opt/penny NIXL_CONFIG=/root/nixl-posix.toml
# Boot from the prepacked flat file on the volume (falls back to a normal load
# plus a dump when the pack is missing or its key changed).
export SGLANG_PREPACKED_DIR="$PENNY_MODEL_DIR/prepacked"
# $SGLANG_ARGS_EXTRA arrives as the control plane's llm.sglang_args, delivered at create: a
# tuning flag is a settings edit and the next pod, never a new tag. It goes in the middle so the
# image's own invariants win — the prepacked load, and --cuda-graph-bs 1 2 last (builds run at
# most 2 streams; the serve script's baked "1 2 4" loses to the later occurrence). A flag that
# changes the FlashInfer autotune key costs one ~300 s re-tune per pod, so a flag set there
# should be measured on one pod first.
export SGLANG_ARGS_EXTRA="--load-format prepacked ${SGLANG_ARGS_EXTRA:-} --cuda-graph-bs 1 2"
mkdir -p /root/nixl
target="http://127.0.0.1:$SGLANG_PORT"

# First launches OOM-SIGKILL under the 188 GB container cgroup roughly half the
# time (mmap'd checkpoint + PLE offload + load buffers); a relaunch reliably
# loads, so the engine gets three attempts before the pod gives up.
healthy=""
for attempt in 1 2 3; do
    mark "engine launch attempt $attempt"
    bash /root/serve-nohicache.sh &
    engine_pid=$!
    for _ in $(seq 1 200); do
        curl -sf -o /dev/null "$target/health_generate" && { healthy=1; break; }
        kill -0 "$engine_pid" 2>/dev/null || { mark "engine died during startup (attempt $attempt)"; break; }
        sleep 3
    done
    [ -n "$healthy" ] && break
    pkill -9 -f "sglang" 2>/dev/null || true
    sleep 5
done
[ -n "$healthy" ] || { echo "engine never became healthy after 3 attempts" >&2; exit 1; }
mark "pennyroyal up as $LLM_MODEL"

/opt/venv/bin/python3 -m worker.agent \
    --server "$CP_URL" \
    --queue llm \
    --target "$target" \
    --source runpod \
    ${IDLE_EXIT_SECONDS:+--idle-exit-seconds "$IDLE_EXIT_SECONDS"} &
agent_pid=$!

trap 'kill -TERM "$agent_pid" 2>/dev/null || true' TERM INT
set +e
wait "$agent_pid"; rc=$?
if [ "$rc" -gt 128 ]; then wait "$agent_pid"; rc=$?; fi
set -e

if [ "$rc" -eq 0 ] && [ -n "${RUNPOD_POD_ID:-}" ] && [ -n "${RUNPOD_API_KEY:-}" ]; then
    echo "clean exit — self-terminating pod $RUNPOD_POD_ID"
    for _ in 1 2 3; do
        code=$(curl -s -o /tmp/selfterm.out -w '%{http_code}' -X DELETE \
            "https://rest.runpod.io/v1/pods/$RUNPOD_POD_ID" \
            -H "Authorization: Bearer $RUNPOD_API_KEY")
        case "$code" in 2*|404) echo "self-terminate accepted (HTTP $code)"; break ;; esac
        sleep 2
    done
fi
exit "$rc"
