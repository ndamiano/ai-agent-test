#!/usr/bin/env bash
# Stage weights off the FUSE volume, start ComfyUI, run one warmup render, THEN hand the
# container to the worker agent.
#
# The staging + warmup exist because of how the volume behaves under mmap: bulk reads stream at
# 2.7GB/s, but safetensors' mmap page-faults re-read at ~200MB/s and the FUSE mount keeps no page
# cache (measured on the mesh pod, same volume). Left alone, the FIRST claimed render paid the
# whole flux load that way — 70.3s billed to a user's game for 2s of sampling (measured
# 2026-08-02). Copying once into tmpfs and loading from RAM is the only version the filesystem
# cannot undo; the warmup render then pays the model load + first-use kernels at boot, so the
# worker registers only when a job would run at steady speed. Registration IS the gate: nothing
# can claim before the agent starts.
#
# ComfyUI binds 127.0.0.1 and no port is exposed: it has no auth of its own, so a reachable
# instance is an unauthenticated GPU. The worker dials OUT to the control plane.
set -euo pipefail

: "${CP_URL:?CP_URL (control plane base URL) is required}"
: "${WORKER_TOKEN:?WORKER_TOKEN is required}"

boot_t0=$(date +%s)
mark() { echo "[boot +$(( $(date +%s) - boot_t0 ))s] $*"; }

VOL_MODELS=/workspace/comfy/models
STAGE_DIR="${COMFY_STAGE_DIR:-/dev/shm/comfy-models}"
TREE=/opt/comfy-models

test -e "$VOL_MODELS/checkpoints" || {
    echo "network volume not mounted: $VOL_MODELS is missing" >&2; exit 1; }

# The models tree ComfyUI reads (/opt/ComfyUI/models points here): every folder is the volume's,
# except the two that hold weights this pod will actually load — those point at the staged copy
# when staging succeeds. Built before ComfyUI starts; the staged links dangle until the copy
# lands, which is fine because nothing resolves them before the warmup submit.
mkdir -p "$TREE"
for d in "$VOL_MODELS"/*/; do
    ln -sfn "${d%/}" "$TREE/$(basename "$d")"
done

# Stage in the background while ComfyUI imports torch — the copy is shorter than the import, so
# it rides free. The python does a chunked parallel copy: one 17GB file in single-stream cp reads
# well below the 2.7GB/s the volume serves to concurrent readers.
python - "$VOL_MODELS" "$STAGE_DIR" "$TREE" <<'PY' &
import os, shutil, sys, time
from concurrent.futures import ThreadPoolExecutor

vol, stage, tree = sys.argv[1], sys.argv[2], sys.argv[3]
# What this pod loads: the flux checkpoint (the one ckpt_name every live workflow names) and the
# BiRefNet matte weights. Everything else on the volume is other queues' or dead paths.
flux = os.path.join(vol, "checkpoints", "flux1-schnell-fp8.safetensors")
rmbg = os.path.join(vol, "RMBG")

def tree_size(p):
    if os.path.isfile(p):
        return os.path.getsize(p)
    return sum(os.path.getsize(os.path.join(r, f))
               for r, _, fs in os.walk(p) for f in fs)

def copy_chunked(src, dst, chunk=1 << 30, streams=8):
    size = os.path.getsize(src)
    with open(dst, "wb") as f:
        f.truncate(size)
    def part(off):
        with open(src, "rb", buffering=0) as fin, open(dst, "r+b", buffering=0) as fout:
            fin.seek(off); fout.seek(off)
            left = min(chunk, size - off)
            while left:
                buf = fin.read(min(1 << 26, left))
                fout.write(buf)
                left -= len(buf)
    with ThreadPoolExecutor(max_workers=streams) as pool:
        list(pool.map(part, range(0, size, chunk)))

t0 = time.time()
try:
    need = tree_size(flux) + tree_size(rmbg)
    os.makedirs(stage, exist_ok=True)
    free = shutil.disk_usage(stage).free
    if free < need * 1.1:
        print(f"[stage] skipped: {free/1e9:.1f} GB free at {stage}, need {need*1.1/1e9:.1f} GB "
              f"— loading off the volume", flush=True)
        sys.exit(0)
    os.makedirs(os.path.join(stage, "checkpoints"), exist_ok=True)
    copy_chunked(flux, os.path.join(stage, "checkpoints", os.path.basename(flux)))
    shutil.copytree(rmbg, os.path.join(stage, "RMBG"), dirs_exist_ok=True)
except OSError as e:
    print(f"[stage] failed ({e}) — loading off the volume", flush=True)
    sys.exit(0)

# Point the live tree at the staged copies only after both landed whole.
for name in ("checkpoints", "RMBG"):
    link = os.path.join(tree, name)
    tmp = link + ".new"
    os.symlink(os.path.join(stage, name), tmp)
    os.replace(tmp, link)
dt = time.time() - t0
print(f"[stage] staged {need/1e9:.1f} GB to {stage} in {dt:.1f}s ({need/dt/1e6:.0f} MB/s)",
      flush=True)
PY
stage_pid=$!

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
mark "ComfyUI up: $(curl -s "http://127.0.0.1:$COMFY_PORT/system_stats" | head -c 300)"

wait "$stage_pid" || true
mark "staging settled"

# Warmup render — the same graph a sprite job runs (flux + the BiRefNet matte), one step at
# 256px, so the checkpoint load, the matte's lazy import and the first-use CUDA kernels are all
# paid HERE, before the worker can claim. A pod that cannot render dies at boot instead of
# failing a user's job.
python - "$COMFY_PORT" <<'PY'
import json, sys, time, urllib.request, uuid

port = sys.argv[1]
base = f"http://127.0.0.1:{port}"
wf = {
    "4": {"class_type": "CheckpointLoaderSimple",
          "inputs": {"ckpt_name": "flux1-schnell-fp8.safetensors"}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "warmup", "clip": ["4", 1]}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}},
    "5": {"class_type": "EmptySD3LatentImage",
          "inputs": {"width": 256, "height": 256, "batch_size": 1}},
    "3": {"class_type": "KSampler",
          "inputs": {"seed": 0, "steps": 1, "cfg": 1.0, "sampler_name": "euler",
                     "scheduler": "simple", "denoise": 1.0, "model": ["4", 0],
                     "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    "47": {"class_type": "BiRefNetRMBG",
           "inputs": {"model": "BiRefNet-general", "mask_blur": 0, "mask_offset": -1,
                      "invert_output": False, "refine_foreground": False,
                      "background": "Alpha", "background_color": "#ffffff",
                      "image": ["8", 0]}},
    "9": {"class_type": "PreviewImage", "inputs": {"images": ["47", 0]}},
}
body = json.dumps({"prompt": wf, "client_id": str(uuid.uuid4())}).encode()
req = urllib.request.Request(f"{base}/prompt", data=body,
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=60) as r:
    pid = json.load(r)["prompt_id"]

deadline = time.time() + 240
while time.time() < deadline:
    with urllib.request.urlopen(f"{base}/history/{pid}", timeout=10) as r:
        hist = json.load(r).get(pid)
    if hist:
        status = hist.get("status", {})
        if status.get("status_str") == "error":
            sys.exit(f"warmup render errored: {json.dumps(status)[:500]}")
        if hist.get("outputs"):
            sys.exit(0)
    time.sleep(2)
sys.exit("warmup render did not finish within 240s")
PY
mark "warmup render done — registering"

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
