#!/usr/bin/env bash
# Stage weights off the FUSE volume, start ComfyUI, run one warmup render, THEN hand the
# container to the worker agent. One image serves two queues: `WORKER_QUEUE=image` (stills) or
# `WORKER_QUEUE=video` (MiniMax-H3 sprite sheets) — same ComfyUI, different weights staged and
# warmed, a different queue pulled.
#
# The staging + warmup exist because of how the volume behaves under mmap: bulk reads stream at
# 2.7GB/s, but safetensors' mmap page-faults re-read at ~200MB/s and the FUSE mount keeps no page
# cache (measured on the mesh pod, same volume). Left alone, the FIRST claimed render paid the
# whole flux load that way — 70.3s billed to a user's game for 2s of sampling (measured
# 2026-08-02). Copying once onto the container disk — a real filesystem, whose page cache holds —
# is the only version the volume cannot undo; the warmup render then pays the model load +
# first-use kernels at boot, so the worker registers only when a job would run at steady speed.
# Registration IS the gate: nothing can claim before the agent starts. The stage is not tmpfs:
# /dev/shm on a RunPod host is a fixed ~46 GB whatever the RAM, under both queues' weights, and a
# stage that does not fit is silently a 3.5-minute load off the mount (measured 2026-09-04).
#
# ComfyUI binds 127.0.0.1 and no port is exposed: it has no auth of its own, so a reachable
# instance is an unauthenticated GPU. The worker dials OUT to the control plane.
set -euo pipefail

: "${CP_URL:?CP_URL (control plane base URL) is required}"
: "${WORKER_TOKEN:?WORKER_TOKEN is required}"
WORKER_QUEUE="${WORKER_QUEUE:-image}"
case "$WORKER_QUEUE" in image|video) ;; *) echo "WORKER_QUEUE must be image or video, not '$WORKER_QUEUE'" >&2; exit 1 ;; esac

boot_t0=$(date +%s)
mark() { echo "[boot +$(( $(date +%s) - boot_t0 ))s] $*"; }

VOL_MODELS=/workspace/comfy/models
STAGE_DIR="${COMFY_STAGE_DIR:-/stage/comfy-models}"
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
python - "$VOL_MODELS" "$STAGE_DIR" "$TREE" "$WORKER_QUEUE" <<'PY' &
import os, shutil, sys, time
from concurrent.futures import ThreadPoolExecutor

vol, stage, tree, queue = sys.argv[1:5]
# What this pod loads: every weight the queue's live workflows name. The image queue: the tile
# checkpoint, the two Qwen unets with their shared encoder and VAE, and the BiRefNet matte. The
# video queue: the MiniMax-H3 image-to-video unet, its own Qwen3-VL encoder and video VAE.
# Everything else on the volume belongs to other queues.
FOLDERS = {
    "image": {
        "checkpoints": ["DreamShaperXL_Turbo_v2_1.safetensors"],
        "diffusion_models": ["qwen_image_2512_fp8_e4m3fn.safetensors",
                             "qwen_image_edit_2511_fp8mixed.safetensors"],
        "text_encoders": ["qwen_2.5_vl_7b_fp8_scaled.safetensors"],
        "vae": ["qwen_image_vae.safetensors"],
    },
    "video": {
        "diffusion_models": ["minimax_h3_fl2va_pruned_int8_convrot.safetensors"],
        "text_encoders": ["qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"],
        "vae": ["minimax_h3_video_vae_fp16.safetensors"],
    },
}[queue]
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
    files = [(folder, os.path.join(vol, folder, n))
             for folder, names in FOLDERS.items() for n in names]
    need = sum(tree_size(p) for _, p in files) + tree_size(rmbg)
    os.makedirs(stage, exist_ok=True)
    free = shutil.disk_usage(stage).free
    if free < need * 1.1:
        print(f"[stage] skipped: {free/1e9:.1f} GB free at {stage}, need {need*1.1/1e9:.1f} GB "
              f"— loading off the volume", flush=True)
        sys.exit(0)
    for folder, src in files:
        os.makedirs(os.path.join(stage, folder), exist_ok=True)
        copy_chunked(src, os.path.join(stage, folder, os.path.basename(src)))
    shutil.copytree(rmbg, os.path.join(stage, "RMBG"), dirs_exist_ok=True)
except OSError as e:
    print(f"[stage] failed ({e}) — loading off the volume", flush=True)
    sys.exit(0)

# Point the live tree at the staged copies only after all of them landed whole.
for name in (*FOLDERS, "RMBG"):
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

# Warmup render — the graph the queue's jobs run, one step at the smallest size the model takes:
# a sprite (the Qwen subject graph + the BiRefNet matte) for the image queue, a 5-frame
# image-to-video clip for the video queue — so the weight load, the matte's lazy import and the
# first-use CUDA kernels are all paid HERE, before the worker can claim. A pod that cannot render
# dies at boot instead of failing a user's job.
python - "$COMFY_PORT" "$WORKER_QUEUE" <<'PY'
import json, struct, sys, time, urllib.request, uuid, zlib

port, queue = sys.argv[1], sys.argv[2]
base = f"http://127.0.0.1:{port}"
if queue == "image":
    wf = {
        "u": {"class_type": "UNETLoader",
              "inputs": {"unet_name": "qwen_image_2512_fp8_e4m3fn.safetensors",
                         "weight_dtype": "default"}},
        "c": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                         "type": "qwen_image", "device": "default"}},
        "v": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "ms": {"class_type": "ModelSamplingAuraFlow", "inputs": {"shift": 3.1, "model": ["u", 0]}},
        "p": {"class_type": "CLIPTextEncode", "inputs": {"text": "warmup", "clip": ["c", 0]}},
        "n": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["c", 0]}},
        "l": {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": 256, "height": 256, "batch_size": 1}},
        "k": {"class_type": "KSampler",
              "inputs": {"seed": 0, "steps": 1, "cfg": 2.5, "sampler_name": "euler",
                         "scheduler": "simple", "denoise": 1.0, "model": ["ms", 0],
                         "positive": ["p", 0], "negative": ["n", 0], "latent_image": ["l", 0]}},
        "d": {"class_type": "VAEDecode", "inputs": {"samples": ["k", 0], "vae": ["v", 0]}},
        "m": {"class_type": "BiRefNetRMBG",
              "inputs": {"model": "BiRefNet-general", "mask_blur": 0, "mask_offset": -1,
                         "invert_output": False, "refine_foreground": False,
                         "background": "Alpha", "background_color": "#ffffff",
                         "image": ["d", 0]}},
        "s": {"class_type": "PreviewImage", "inputs": {"images": ["m", 0]}},
    }
else:
    # A 256px white PNG, uploaded as the clip's pinned first frame: the i2v graph has no
    # empty-latent entry and LoadImage reads only ComfyUI's input folder.
    raw = b"".join(b"\x00" + b"\xff" * (256 * 3) for _ in range(256))
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 256, 256, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
            f"filename=\"warmup.png\"\r\nContent-Type: image/png\r\n\r\n").encode() + png + \
           f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{base}/upload/image", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        still = json.load(r)["name"]
    wf = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
                         "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
                         "type": "minimax"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
        "4": {"class_type": "LoadImage", "inputs": {"image": still}},
        "5": {"class_type": "MiniMaxH3ImageToVideo",
              "inputs": {"prompt": "warmup", "width": 256, "height": 256, "length": 5,
                         "clip": ["2", 0], "vae": ["3", 0],
                         "first_frame": ["4", 0], "last_frame": ["4", 0]}},
        "6": {"class_type": "MiniMaxH3SigmaShift",
              "inputs": {"shift_video": 12.0, "shift_audio": 3.0, "model": ["1", 0]}},
        "7": {"class_type": "BasicGuider", "inputs": {"model": ["6", 0], "conditioning": ["5", 0]}},
        "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "9": {"class_type": "BasicScheduler",
              "inputs": {"scheduler": "simple", "steps": 1, "denoise": 1.0, "model": ["6", 0]}},
        "10": {"class_type": "RandomNoise", "inputs": {"noise_seed": 0}},
        "11": {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["10", 0], "guider": ["7", 0], "sampler": ["8", 0],
                          "sigmas": ["9", 0], "latent_image": ["5", 1]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 1], "vae": ["3", 0]}},
        "13": {"class_type": "PreviewImage", "inputs": {"images": ["12", 0]}},
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
    --queue "$WORKER_QUEUE" \
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
