#!/usr/bin/env bash
# Populate a RunPod network volume with every model weight the three GPU queues load.
#
# Run this ON a pod with the volume mounted at $VOL (default /workspace). Any cheap pod will do —
# this phase is pure download, no GPU touched. Downloads come from HuggingFace at datacenter
# bandwidth; nothing is copied from the home box.
#
#   VOL=/workspace bash scripts/provision_volume.sh
#
# Idempotent: a file already present at its expected byte size is skipped, so a re-run after an
# interrupted pull resumes rather than restarts. Sizes are the bytes observed on the home box —
# a mismatch fails loudly rather than leaving a half file that a backend loads as corrupt.

set -euo pipefail
VOL="${VOL:-/workspace}"

# Pod images ship several interpreters and only some own a pip. Install and run under the SAME one
# or the download imports nothing — and pip's own shebang names that interpreter, which on these
# images is often not any python3 on PATH. hf_transfer is its own package (the extra is gone in 1.x).
PY=""
if command -v pip >/dev/null 2>&1; then
    cand=$(head -1 "$(command -v pip)" | sed 's|^#!||; s| .*||')
    [ -x "$cand" ] && "$cand" -m pip --version >/dev/null 2>&1 && PY="$cand"
fi
for c in python3.13 python3.12 python3.11 python3 python; do
    [ -n "$PY" ] && break
    command -v "$c" >/dev/null 2>&1 || continue
    "$c" -m pip --version >/dev/null 2>&1 || continue
    PY="$c"; break
done
if [ -z "$PY" ]; then
    PY=python3
    "$PY" -m ensurepip --upgrade || curl -sS https://bootstrap.pypa.io/get-pip.py | "$PY"
fi
echo "using interpreter: $("$PY" -c 'import sys; print(sys.executable, sys.version.split()[0])')"
"$PY" -m pip install -q --upgrade huggingface_hub hf_transfer || \
    "$PY" -m pip install -q --upgrade --break-system-packages huggingface_hub hf_transfer
export HF_HUB_ENABLE_HF_TRANSFER=1
export HF_HOME="$VOL/hf-cache"

mkdir -p "$VOL/models/LLM" "$VOL/models/ninfer" "$VOL/comfy/models/checkpoints" \
         "$VOL/comfy/models/diffusion_models" "$VOL/comfy/models/text_encoders" \
         "$VOL/comfy/models/vae" \
         "$VOL/comfy/models/RMBG/BiRefNet" "$VOL/trellis2-weights" "$VOL/hf-cache"

"$PY" - "$VOL" <<'PY'
import os, shutil, sys
from huggingface_hub import hf_hub_download, snapshot_download

VOL = sys.argv[1]

# dest dir, repo id, filename in repo, expected bytes (0 = skip the size check), revision
#
# The LLM is pinned to a commit, not `main`. It is the DENSE 27B, not the 35B-A3B MoE this volume
# was first provisioned with: the MoE is faster per token, but the window is what a build lives or
# dies on and the 27B's is what the box was moved to.
#
# The SAME model ships twice, in two containers, because a pod's card decides which engine can run
# it: ninfer serves ~60% more tok/s but is compiled for sm_120a and reads only its own artifact,
# while llama.cpp reads only the GGUF and runs anywhere. Both live here so one image can pick at
# boot — the volume holds ~34 GiB for one model, which is the price of not caring which card
# RunPod hands out.
FILES = [
    (f"{VOL}/models/LLM", "unsloth/Qwen3.6-27B-GGUF",
     "Qwen3.6-27B-UD-Q4_K_XL.gguf", 17612564704,
     "82d411acf4a06cfb8d9b073a5211bf410bfc29bf"),
    (f"{VOL}/models/ninfer", "neroued/Qwen3.6-27B-nvfp4-NInfer",
     "qwen3_6_27b_nvfp4.ninfer", 18324064000,
     "610abfadfd8102791f1a7317d243463843e14758"),
    # The image queue's four checkpoints, one per kind (see maestro/codegen: sprites/scenes ->
    # NetaYume, tiles + scene-chain terrain -> DreamShaperXL Turbo, scene-chain subjects ->
    # Qwen-Image-2512, the scene embed -> Qwen-Image-Edit-2511). The two Qwen graphs share the
    # VL text encoder and the VAE.
    (f"{VOL}/comfy/models/checkpoints", "duongve/NetaYume-Lumina-Image-2.0",
     "NetaYume_v4_all_in_one.safetensors", 10620229821, None),
    (f"{VOL}/comfy/models/checkpoints", "Lykon/dreamshaper-xl-v2-turbo",
     "DreamShaperXL_Turbo_v2_1.safetensors", 6939220250, None),
    (f"{VOL}/comfy/models/diffusion_models", "Comfy-Org/Qwen-Image_ComfyUI",
     "split_files/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors", 20430679144, None),
    (f"{VOL}/comfy/models/diffusion_models", "Comfy-Org/Qwen-Image-Edit_ComfyUI",
     "split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors", 20533762817, None),
    (f"{VOL}/comfy/models/text_encoders", "Comfy-Org/Qwen-Image_ComfyUI",
     "split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors", 9384670680, None),
    (f"{VOL}/comfy/models/vae", "Comfy-Org/Qwen-Image_ComfyUI",
     "split_files/vae/qwen_image_vae.safetensors", 253806246, None),
    (f"{VOL}/comfy/models/RMBG/BiRefNet", "1038lab/BiRefNet",
     "BiRefNet-general.safetensors", 0, None),
    (f"{VOL}/comfy/models/RMBG/BiRefNet", "1038lab/BiRefNet", "birefnet.py", 0, None),
    (f"{VOL}/comfy/models/RMBG/BiRefNet", "1038lab/BiRefNet", "BiRefNet_config.py", 0, None),
    (f"{VOL}/comfy/models/RMBG/BiRefNet", "1038lab/BiRefNet", "config.json", 0, None),
]

for dest, repo, name, want, rev in FILES:
    # ComfyUI resolves weights by folder, so a repo that nests its files (Comfy-Org's
    # split_files/) lands FLAT here — the name in the workflow is the basename.
    path = os.path.join(dest, os.path.basename(name))
    if os.path.exists(path) and (want == 0 or os.path.getsize(path) == want):
        print(f"ok (present)  {name}")
        continue
    print(f"downloading   {name}  <- {repo}")
    got = hf_hub_download(repo_id=repo, filename=name, local_dir=dest, revision=rev)
    if got != path:
        os.replace(got, path)
    got = os.path.getsize(path)
    if want and got != want:
        sys.exit(f"SIZE MISMATCH {name}: got {got}, expected {want} — wrong repo or a bad pull")
    print(f"ok            {name}  ({got/1e9:.1f} GB)")

print("downloading   TRELLIS.2-4B weights (~16 GB)")
snapshot_download("microsoft/TRELLIS.2-4B", local_dir=f"{VOL}/trellis2-weights")

# The worker image runs HF_HUB_OFFLINE=1 — after provisioning, a pod NEVER talks to HF. Stock
# pipeline configs reference encoders by HUB NAME (facebook/dinov3-*: gated; briaai/RMBG-2.0:
# restrictive license), which both violates that and can break under someone else's gating
# decision. So: pull open-licensed equivalents into plain dirs on the volume (camenduru mirror =
# same dinov3 weights, public; ZhengPeng7/BiRefNet = the permissive rembg the home box uses) and
# rewrite the configs to those paths. Paths are the POD's mount point (/workspace) verbatim.
print("downloading   encoders (dinov3 mirror + BiRefNet)")
snapshot_download("camenduru/dinov3-vitl16-pretrain-lvd1689m",
                  local_dir=f"{VOL}/encoders/dinov3-vitl16")
snapshot_download("ZhengPeng7/BiRefNet", local_dir=f"{VOL}/encoders/BiRefNet")
for cfg in ("pipeline.json", "texturing_pipeline.json"):
    p = f"{VOL}/trellis2-weights/{cfg}"
    s = open(p).read() \
        .replace("facebook/dinov3-vitl16-pretrain-lvd1689m", "/workspace/encoders/dinov3-vitl16") \
        .replace("briaai/RMBG-2.0", "/workspace/encoders/BiRefNet")
    open(p, "w").write(s)
    print(f"patched       {cfg}: hub names -> /workspace/encoders/*")

# local_dir downloads leave a .cache/huggingface staging dir beside the weights. The volume holds
# weights and nothing else, so drop them — a re-run re-downloads whole rather than resuming, which
# is the trade this rule buys.
for root, dirs, _ in os.walk(VOL):
    if ".cache" in dirs and not root.startswith(f"{VOL}/hf-cache"):
        shutil.rmtree(os.path.join(root, ".cache"))
        dirs.remove(".cache")

# TRELLIS pulls these at pipeline load; pre-seed HF_HOME so the first mesh job doesn't stall.
# transformers loads ONE weight format, and these repos ship four — an unfiltered snapshot took
# 6.9 GB of volume for the 1.6 GB safetensors it reads.
IGNORE = ["*.bin", "*.msgpack", "*.h5", "*.onnx", "*.onnx_data"]
for repo in ("openai/clip-vit-large-patch14",
             "camenduru/dinov3-vitl16-pretrain-lvd1689m"):
    print(f"pre-seeding   {repo}")
    try:
        snapshot_download(repo, ignore_patterns=IGNORE)
    except Exception as e:
        print(f"  skipped ({e}) — will fetch at runtime")
PY

echo
# du only — a RunPod network volume is MooseFS-backed, so df reports the whole cluster, never
# this volume's usage or quota.
echo "== volume contents =="
du -sh "$VOL/models/LLM" "$VOL/models/ninfer" "$VOL/comfy/models" "$VOL/trellis2-weights" \
       "$VOL/hf-cache" "$VOL"
echo "== anything that is not a weight (expect only the two BiRefNet .py) =="
find "$VOL" \( -name .cache -o -name "*.py" -o -name "__pycache__" \) -maxdepth 6
