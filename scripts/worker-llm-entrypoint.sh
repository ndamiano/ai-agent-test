#!/usr/bin/env bash
# Start the inference engine THIS CARD can run, wait for it, then hand the container over to the
# worker agent.
#
# Both engines bind 127.0.0.1: nothing about this pod is reachable from outside. The worker dials
# OUT to the control plane, so the pod needs no exposed port and no inbound rule. An llm-server
# reachable from the internet is an unauthenticated GPU.
set -euo pipefail

: "${CP_URL:?CP_URL (control plane base URL) is required}"
: "${WORKER_TOKEN:?WORKER_TOKEN is required}"
# The control plane's llm.model, verbatim. llama.cpp's router SELECTS the GGUF by it and ninfer
# REJECTS any request whose model is not its --model-id, so the same string has to serve as file
# name there and alias here.
: "${LLM_MODEL:?LLM_MODEL is required — it is what the control plane names in every request}"

boot_t0=$(date +%s)
mark() { echo "[boot +$(( $(date +%s) - boot_t0 ))s] $*"; }

# The engine each branch started, and where it answers. `wait_engine URL PATH TRIES` polls the
# health path and dies loudly if the process goes before it answers.
engine_pid=""
target=""

wait_engine() {
    local url="$1" path="$2" tries="$3"
    for _ in $(seq 1 "$tries"); do
        if curl -sf "$url$path" >/dev/null; then return 0; fi
        kill -0 "$engine_pid" 2>/dev/null || { echo "engine died during startup" >&2; exit 1; }
        sleep 2
    done
    echo "engine did not answer $path within $((tries * 2))s" >&2
    exit 1
}

start_ninfer() {
    # --spec mtp + --lm-head-draft are speculative decode; --no-thinking is the floor the
    # llama.cpp branch gets from enable_thinking=false. Residency is frozen at start, so what is
    # NOT passed (vision, dflash) is weights this process never allocates.
    #
    # --presence-penalty 0 is load-bearing, not tidying: ninfer's sampler defaults to Qwen3
    # THINKING defaults, presence-penalty 1.0 among them, and a penalty on repeated tokens
    # degrades exactly what a build turn emits — long structured output over a fixed vocabulary of
    # paths, braces and identifiers. The canonical request carries no penalty field, so the launch
    # flag is where it can be said.
    ninfer-serve "$NINFER_MODEL" \
        --model-id "$LLM_MODEL" \
        --host 127.0.0.1 --port "$NINFER_PORT" \
        --max-context 131072 \
        --spec mtp --draft-tokens 3 --lm-head-draft \
        --presence-penalty 0 \
        --no-thinking &
    engine_pid=$!
    target="http://127.0.0.1:$NINFER_PORT"
    # ninfer answers nothing until the whole artifact is resident, so this waits on a 17 GiB pull
    # off the network volume, not on a process.
    wait_engine "$target" /health 150
    mark "ninfer up as $LLM_MODEL: $(curl -s "$target/v1/models" | head -c 400)"
}

start_llama_cpp() {
    # Router mode: children inherit these, so they match the home box's serving config exactly.
    # --chat-template-kwargs: --reasoning-budget 0 is a NO-OP for Qwen3.6's template — the model
    # thinks in content and truncates at the output cap before ever emitting the message/tool call
    # (measured: whole authoring turns lost as 16K-token reasoning blobs). enable_thinking=false
    # switches the template itself; verified locally: zero reasoning tokens, clean tool calls.
    #
    # -c must match LLM_N_CTX on the control plane: it is what the input budget is computed from,
    # and a window larger than the server's is one that never trims until the prompt has already
    # overflowed. llama.cpp preallocates ALL of it at load, so the KV quant is not an optimisation
    # here but what decides whether the model loads at all: the dense 27B keeps 64 layers × 4 kv
    # heads × 256, which is 136 KiB/token at q8_0 — 17.0 GiB for this window, over a 32 GiB card
    # once the weights are in. q4_0 halves it. -fa is required for the quantized cache to be used.
    /app/llama-server \
        --models-dir "$MODELS_DIR" \
        --host 127.0.0.1 --port "$LLAMA_PORT" \
        -ngl 99 -c 131072 -fa on \
        --cache-type-k q4_0 --cache-type-v q4_0 \
        --jinja --reasoning-budget 0 \
        --chat-template-kwargs '{"enable_thinking":false}' &
    engine_pid=$!
    target="http://127.0.0.1:$LLAMA_PORT"
    # The router answers /models before any weights load (models load on first request), so this
    # waits for the process, not for the 16 GB pull off the network volume.
    wait_engine "$target" /models 60
    mark "llama-server up: $(curl -s "$target/models" | head -c 400)"
}

gpu=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || true)
driver=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 || true)
mark "card: ${gpu:-unknown}  driver: ${driver:-unknown}"

# ninfer serves the same model ~60% faster and is compiled for sm_120a — a 5090 and nothing else,
# so this is a capability test, not a preference. The driver is PART of the capability: ninfer is
# a CUDA 13.1 build and needs the host driver at r580+ — RunPod hosts vary, and on an older one
# ninfer dies at cudaGetDeviceCount (measured 2026-08-01: a 5090 pod boot-looping on
# cudaErrorInsufficientDriver until the boot-deadline reaper collected it). A 5090 behind an old
# driver serves the GGUF instead — slower beats a pod that bills and never claims. Any other card
# serves the GGUF through llama.cpp, which is why both artifacts sit on the volume.
if [[ "$gpu" == *"5090"* ]] && [ -f "$NINFER_MODEL" ] && [ "${driver%%.*}" -ge 580 ] 2>/dev/null; then
    start_ninfer
else
    start_llama_cpp
fi

# No `exec`: RunPod restarts an exited container and keeps billing (even exit 0), so a clean
# agent exit must be followed by an API pod kill. Best-effort here (fires only when the pod env
# carries RUNPOD_API_KEY — RunPod injects no key on its own); the
# control-plane reaper is the billing guarantee. Nonzero exits pass through — RunPod's restart
# is free crash recovery.
python3 -m worker.agent \
    --server "$CP_URL" \
    --queue llm \
    --target "$target" \
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
