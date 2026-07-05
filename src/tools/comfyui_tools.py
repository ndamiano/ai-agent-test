"""ComfyUI tools for image generation"""

import json
import uuid
import time
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Any, List, Optional

import urllib.request
import urllib.parse
import urllib.error

from tools.tool_manager import tool_manager

logger = logging.getLogger(__name__)

_WORKFLOWS_DIR = Path(__file__).parent.parent / "config" / "workflows"
_TXT2IMG_CHARACTER_WORKFLOW_PATH = _WORKFLOWS_DIR / "txt2img_character.json"
_IMG2IMG_CHARACTER_WORKFLOW_PATH = _WORKFLOWS_DIR / "img2img_character.json"
_TXT2IMG_BACKGROUND_WORKFLOW_PATH = _WORKFLOWS_DIR / "txt2img_background.json"
_TXT2IMG_TILE_WORKFLOW_PATH = _WORKFLOWS_DIR / "txt2img_tile.json"
_TXT2IMG_ITEM_WORKFLOW_PATH = _WORKFLOWS_DIR / "txt2img_item.json"
_TXT2IMG_WORKFLOW_PATH = _WORKFLOWS_DIR / "txt2img.json"

# ---------------------------------------------------------------------------
# Prompt construction helpers
# ---------------------------------------------------------------------------

# Anima (animaOfficial_preview3Base) uses a Qwen text encoder — it reads natural
# language, not Pony/booru tag salad. score_9/score_8_up etc. are Pony embeddings
# the Qwen encoder doesn't know, so prompts are plain descriptive sentences.
_STYLE_LOCK = (
    "Anime visual novel art style with clean lineart, flat cel shading, and soft colors"
)
_CHAR_COMPOSITION = (
    "Full body view of the character standing in a relaxed neutral pose, arms at their "
    "sides, facing the viewer, against a plain white background."
)
_QUALITY = "masterpiece, best quality, highly detailed"

_CHAR_NEGATIVE = (
    "low quality, blurry, distorted, extra limbs, bad anatomy, bad hands, "
    "background scenery, multiple characters, cropped, watermark, signature, text"
)

# Backgrounds render on WAI Illustrious (SDXL), a cel-shaded anime model — so scenes MATCH the
# anime sprite style instead of clashing with the photoreal look the Qwen/Anima model gives. It
# reads booru-style quality tags, hence the leading tag salad (unlike the Anima prompts).
_BG_QUALITY = "masterpiece, best quality, amazing quality, newest, absurdres"
_BG_NEGATIVE = (
    "worst quality, low quality, blurry, distorted, people, person, 1girl, 1boy, "
    "characters, figures, monster, creature, animal, insect, skeleton, subject, "
    "foreground object, close-up, watermark, signature, text, gibberish text, signage, "
    "lettering, posters with text"
)

_CG_NEGATIVE = (
    "low quality, blurry, distorted, bad anatomy, plain white background, "
    "empty scene, watermark, signature, text"
)

_TITLE_CARD_NEGATIVE = (
    "low quality, blurry, distorted, watermark, signature, ui elements, hud, "
    "text, title text, logo, lettering, typography, japanese text, subtitles"
)


def _build_character_prompt(char_data: dict) -> tuple[str, str]:
    description = (char_data.get("description") or char_data.get("name") or "").strip()
    positive = f"{_QUALITY}. {_STYLE_LOCK}. {description} {_CHAR_COMPOSITION}"
    return positive, _CHAR_NEGATIVE


# Emotion variants are img2img'd off the neutral base: same description + composition (so pose
# and identity hold), only the facial-expression clause changes. The base's "relaxed neutral
# pose" wording is dropped here so the expression is the sole variable.
_CHAR_COMPOSITION_EMOTE = (
    "Full body view of the character standing in a relaxed pose, arms at their "
    "sides, facing the viewer, against a plain white background."
)
_EMOTION_PHRASE = {
    "neutral":   "a calm, neutral expression",
    "happy":     "a happy, warm smile",
    "sad":       "a sad, downcast expression",
    "angry":     "an angry, scowling expression",
    "surprised": "a surprised, wide-eyed expression",
    "worried":   "a worried, anxious expression",
}


def _build_character_emotion_prompt(char_data: dict, emotion: str) -> tuple[str, str]:
    description = (char_data.get("description") or char_data.get("name") or "").strip()
    phrase = _EMOTION_PHRASE.get(emotion, _EMOTION_PHRASE["neutral"])
    positive = (f"{_QUALITY}. {_STYLE_LOCK}. {description} {_CHAR_COMPOSITION_EMOTE} "
                f"Their facial expression shows {phrase}.")
    return positive, _CHAR_NEGATIVE


def _build_character_workflow(base_workflow: dict, positive: str, negative: str) -> dict:
    import copy
    wf = copy.deepcopy(base_workflow)
    wf["11"]["inputs"]["text"] = positive
    wf["12"]["inputs"]["text"] = negative
    wf["28"]["inputs"]["width"] = 832
    wf["28"]["inputs"]["height"] = 1216
    wf["19"]["inputs"]["seed"] = int(uuid.uuid4().int % (2**32))
    wf["19"]["inputs"]["cfg"] = 5.0
    wf["19"]["inputs"]["steps"] = 28
    return wf


def _build_widescreen_workflow(base_workflow: dict, positive: str, negative: str) -> dict:
    import copy
    wf = copy.deepcopy(base_workflow)
    wf["11"]["inputs"]["text"] = positive
    wf["12"]["inputs"]["text"] = negative
    wf["28"]["inputs"]["width"] = 1280
    wf["28"]["inputs"]["height"] = 720
    wf["19"]["inputs"]["seed"] = int(uuid.uuid4().int % (2**32))
    wf["19"]["inputs"]["cfg"] = 6.0
    wf["19"]["inputs"]["steps"] = 30
    return wf


def build_character_job(char_data: dict) -> dict:
    """Return a {prompt, workflow_override} job dict for a character's neutral base portrait."""
    positive, negative = _build_character_prompt(char_data)
    return {
        "prompt": positive,
        "workflow_override": _build_character_workflow(_load_workflow(_TXT2IMG_CHARACTER_WORKFLOW_PATH), positive, negative),
    }


def build_token_job(char_data: dict) -> dict:
    """Return a job for a character's OVERWORLD TOKEN — the walkable-map avatar/NPC marker.
    A full-body VN portrait shrunk to one tile reads as a floating cutout; a token is drawn
    for that scale: chibi proportions, bold shapes, square frame. Rides the character workflow
    (BiRefNet matting included) so it lands transparent like the sprites."""
    description = (char_data.get("description") or char_data.get("name") or "").strip()
    positive = (f"{_QUALITY}. Chibi-style full-body game character token of {description} "
                f"Small cute proportions with a large head, standing facing the viewer, "
                f"simple bold shapes readable at a small size, clean thick lineart, flat "
                f"cel shading, against a plain white background.")
    wf = _build_character_workflow(
        _load_workflow(_TXT2IMG_CHARACTER_WORKFLOW_PATH), positive, _CHAR_NEGATIVE)
    wf["28"]["inputs"]["width"] = 832
    wf["28"]["inputs"]["height"] = 832
    return {"prompt": positive, "workflow_override": wf}


def _build_img2img_character_workflow(base_workflow: dict, positive: str, negative: str,
                                      image_name: str, denoise: float) -> dict:
    import copy
    wf = copy.deepcopy(base_workflow)
    wf["11"]["inputs"]["text"] = positive
    wf["12"]["inputs"]["text"] = negative
    wf["28"]["inputs"]["image"] = image_name
    wf["19"]["inputs"]["seed"] = int(uuid.uuid4().int % (2**32))
    wf["19"]["inputs"]["denoise"] = denoise
    return wf


def build_character_emotion_job(char_data: dict, emotion: str, base_image_name: str,
                                denoise: float = 0.5) -> dict:
    """Return a {prompt, workflow_override} job dict for an expression variant, img2img'd off the
    character's already-generated neutral base (uploaded to ComfyUI as `base_image_name`). Low
    denoise holds identity/pose; only the facial expression changes."""
    positive, negative = _build_character_emotion_prompt(char_data, emotion)
    return {
        "prompt": positive,
        "workflow_override": _build_img2img_character_workflow(
            _load_workflow(_IMG2IMG_CHARACTER_WORKFLOW_PATH), positive, negative,
            base_image_name, denoise),
    }


def upload_image(path: str, endpoint: Optional[str] = None) -> str:
    """Upload a local image into ComfyUI's input dir (POST /upload/image) and return the stored
    name LoadImage references. Used to seed img2img from a just-generated neutral sprite."""
    endpoint = (endpoint or _get_comfyui_endpoint()).rstrip("/")
    filename = Path(path).name
    with open(path, "rb") as f:
        file_bytes = f.read()

    boundary = uuid.uuid4().hex
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode(),
        b"Content-Type: application/octet-stream\r\n\r\n",
        file_bytes,
        f"\r\n--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="overwrite"\r\n\r\n',
        b"true",
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(
        f"{endpoint}/upload/image", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        info = json.loads(resp.read())
    name = info.get("name", filename)
    subfolder = info.get("subfolder", "")
    return f"{subfolder}/{name}" if subfolder else name


def _build_background_workflow(base_workflow: dict, positive: str, negative: str) -> dict:
    import copy
    wf = copy.deepcopy(base_workflow)
    wf["6"]["inputs"]["text"] = positive
    wf["7"]["inputs"]["text"] = negative
    wf["3"]["inputs"]["seed"] = int(uuid.uuid4().int % (2**32))
    return wf


def build_background_job(description: str) -> dict:
    """Return a {prompt, workflow_override} job dict for a background image (WAI Illustrious).

    The DESCRIPTION leads the prompt: CLIP weights early tokens hardest and truncates long
    prompts, so with the tag salad in front, the room's actual contents fell off the end
    (observed: 'boxes stacked against walls... posters' rendered as an empty neon server room —
    the mood words survived, the objects didn't). Quality/framing tags ride behind."""
    positive = (f"{description} — scenery, no humans, empty environment, establishing shot, "
                f"wide angle background, {_BG_QUALITY}")
    return {
        "prompt": positive,
        "workflow_override": _build_background_workflow(
            _load_workflow(_TXT2IMG_BACKGROUND_WORKFLOW_PATH), positive, _BG_NEGATIVE),
    }


_ITEM_NEGATIVE = (
    "worst quality, low quality, blurry, distorted, scenery, landscape, environment, room, "
    "wide angle, people, person, 1girl, 1boy, hands, multiple objects, cluttered, cropped, "
    "watermark, signature, text"
)


def build_item_job(description: str) -> dict:
    """Return a {prompt, workflow_override} job dict for ONE inventory item icon: a single
    centered object on a plain background (WAI Illustrious, square — icons render small in the
    inventory bar, so the object must fill the frame, never sit in a scene)."""
    positive = (f"{_BG_QUALITY}, game item icon, a single {description}, one object only, "
                f"centered composition, plain simple background, no scenery, still life, "
                f"clean detailed rendering")
    # txt2img_item = the background workflow + BiRefNet matting: these render ON maps and in
    # the inventory bar, so they must land transparent like sprites (observed: props shipping
    # with baked backgrounds).
    wf = _build_background_workflow(
        _load_workflow(_TXT2IMG_ITEM_WORKFLOW_PATH), positive, _ITEM_NEGATIVE)
    wf["5"]["inputs"]["width"] = 1024
    wf["5"]["inputs"]["height"] = 1024
    return {"prompt": positive, "workflow_override": wf}


def build_feature_job(kind: str, label: str) -> dict:
    """Return a job for ONE map-feature object sprite (a building, fountain, camp... drawn over
    its stamped footprint on the walkable map). High three-quarter view like a JRPG overworld
    object — a straight top-down feature reads as texture, a side view floats. Rides the item
    workflow (BiRefNet matting) so it lands transparent over the terrain."""
    desc = (label or kind.replace("_", " ")).strip()
    positive = (f"{_BG_QUALITY}, a single {desc} seen from a high three-quarter angle, "
                f"stylized 2d game overworld object sprite, whole structure in frame, "
                f"centered, bold readable silhouette, clean detailed rendering, isolated "
                f"against a plain flat white background, no ground, no pedestal, no base "
                f"platform, no scenery around it")
    wf = _build_background_workflow(
        _load_workflow(_TXT2IMG_ITEM_WORKFLOW_PATH), positive, _ITEM_NEGATIVE)
    wf["5"]["inputs"]["width"] = 1024
    wf["5"]["inputs"]["height"] = 1024
    return {"prompt": positive, "workflow_override": wf}


_TILE_NEGATIVE = (
    "map, cartography, chart, diagram, floor plan, blueprint, border, frame, edge, vignette, "
    "text, letters, watermark, logo, people, person, character, figure, animal, object, item, "
    "prop, horizon, sky, clouds, perspective, isometric, depth of field, 3d render, photo of "
    "paper, parchment, table, wall in the distance, room, scene, blurry, jpeg artifacts")

# Formulas from the 48-tile lab (2026-07-04): the old single formula rendered walls as
# rounded river-stones (read as ground). Winners: stylized-tileset language for organic/open
# surfaces; front-facing masonry structure for built barriers — a wall "viewed from above" is
# a losing premise, walls need face-texture treatment to read as walls. WAI Illustrious is
# banned from tiles (hallucinates scenes on texture prompts); tiles stay on DreamShaper.
_TILE_WALLISH = ("wall", "cliff", "palisade", "brick", "masonry", "barricade", "fence",
                 "gate", "rampart", "barrier")
_TILE_FORMULAS = {
    "open": ("top-down 2d game tileset texture of {t}, crisp stylized detail, bold "
             "silhouettes, saturated colors, uniform lighting, orthographic, flat walkable "
             "surface, texture fills frame, tileable game asset"),
    "blocked_organic": ("top-down 2d game tileset texture of {t}, dense impassable growth, "
                        "crisp stylized detail, bold silhouettes, deep shadows between "
                        "elements, darker and denser than open ground, texture fills frame, "
                        "tileable game asset"),
    "blocked_wall": ("flat front-facing pattern of {t}, dense repeating structural detail, "
                     "masonry-like arrangement, strong shadows between elements, reads as an "
                     "impassable barrier, game texture asset, tileable"),
}


# Ideogram4 is trained on structured JSON captions — freeform texture prompts trip its
# "safety" refusals (out-of-distribution), the schema below is what unlocked the best tiles of
# the 84-generation lab. Palettes give per-tile value control; themes with no keyword match
# omit the palette rather than guess.
_IDEO_PALETTES = (
    (("dirt", "earth", "mud", "trail"), ["#6B4A2F", "#7C5A3A", "#8A6A47", "#5A3D26"]),
    (("grass", "moss", "meadow", "leaf"), ["#4F6B35", "#6B8A45", "#3C5228", "#2E401F"]),
    (("bush", "bramble", "hedge", "growth", "pine", "tree", "forest"),
     ["#2E4A22", "#3E5E2C", "#243A1B", "#182812"]),
    (("stone", "cobble", "rock", "masonry", "brick", "granite"),
     ["#7A7A82", "#8E8E96", "#5E5E66", "#3A3A42"]),
    (("wood", "timber", "palisade", "plank", "log"),
     ["#6B4A2F", "#553A24", "#7C5A3A", "#3E2B1A"]),
    (("sand", "dune", "desert"), ["#C9A96A", "#D9BC80", "#B08F55", "#8F7040"]),
    (("snow", "ice", "frozen", "frost"), ["#DCE4EC", "#C2CEDA", "#A8B8C8", "#8FA2B5"]),
    (("water", "stream", "river", "lake"), ["#2E5A7A", "#3E7094", "#24485F", "#182F40"]),
    (("ash", "char", "burnt", "cinder"), ["#4A4A4E", "#5E5A58", "#38363A", "#262428"]),
)

_IDEO_TILE_BODIES = {
    "open": ("A seamless repeating game texture of {t} filling the entire square frame edge "
             "to edge.",
             "A uniform continuous walkable {t} surface covering the whole canvas viewed "
             "straight down, with no objects, no horizon and no border."),
    "blocked_organic": ("A seamless repeating game texture of dense {t} viewed from directly "
                        "above filling the entire square frame edge to edge.",
                        "Thick {t} packed tightly together with deep shadow between clumps, "
                        "denser and darker than open ground, an impassable vegetation pattern "
                        "covering the whole canvas."),
    "blocked_wall": ("A seamless repeating game texture of a {t} face filling the entire "
                     "square frame edge to edge.",
                     "Tightly packed {t} with deep dark shadow seams between elements, a "
                     "dense masonry-like pattern that reads as an impassable barrier, "
                     "covering the whole canvas with no perspective and no horizon."),
}


def _ideogram_tile_caption(theme: str, key: str) -> str:
    hld, bg = (s.format(t=theme) for s in _IDEO_TILE_BODIES[key])
    style = {
        "aesthetics": ("Stylized 2d video game tileset texture, crisp hand-painted detail, "
                       "bold readable shapes, flat orthographic view with no perspective and "
                       "no horizon."),
        "lighting": "Perfectly uniform diffuse lighting with no directional shadows and no "
                    "vignette.",
        "medium": "Digital game asset painting",
    }
    for words, palette in _IDEO_PALETTES:
        if any(w in theme.lower() for w in words):
            style["color_palette"] = palette
            break
    return json.dumps({
        "aspect_ratio": "1:1",
        "high_level_description": hld,
        "style_description": style,
        "compositional_deconstruction": {"background": bg, "elements": []},
    })


def _build_ideogram_tile_workflow(caption: str) -> dict:
    # The lab's canonical graph: conditional + unconditional UNETs through DualModelGuider
    # (asymmetric CFG — 7 dropping to 3 at 70%), Ideogram4Scheduler sigmas, flux2 VAE.
    return {
        "c": {"class_type": "UNETLoader", "inputs": {
            "unet_name": "ideogram4_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "u": {"class_type": "UNETLoader", "inputs": {
            "unet_name": "ideogram4_unconditional_fp8_scaled.safetensors",
            "weight_dtype": "default"}},
        "cl": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "qwen3vl_8b_fp8_scaled.safetensors", "type": "ideogram4",
            "device": "default"}},
        "v": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
        "e": {"class_type": "CLIPTextEncode", "inputs": {"text": caption, "clip": ["cl", 0]}},
        "z": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["e", 0]}},
        "co": {"class_type": "CFGOverride", "inputs": {
            "model": ["c", 0], "cfg": 3.0, "start_percent": 0.7, "end_percent": 1.0}},
        "g": {"class_type": "DualModelGuider", "inputs": {
            "model": ["co", 0], "positive": ["e", 0], "cfg": 7.0,
            "model_negative": ["u", 0], "negative": ["z", 0]}},
        "n": {"class_type": "RandomNoise", "inputs": {
            "noise_seed": int(uuid.uuid4().int % (2**32))}},
        "ks": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "sc": {"class_type": "Ideogram4Scheduler", "inputs": {
            "steps": 20, "width": 1024, "height": 1024, "mu": 0.5, "std": 1.75}},
        "lat": {"class_type": "EmptyFlux2LatentImage", "inputs": {
            "width": 1024, "height": 1024, "batch_size": 1}},
        "s": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["n", 0], "guider": ["g", 0], "sampler": ["ks", 0],
            "sigmas": ["sc", 0], "latent_image": ["lat", 0]}},
        "d": {"class_type": "VAEDecode", "inputs": {"samples": ["s", 0], "vae": ["v", 0]}},
        "o": {"class_type": "SaveImage", "inputs": {
            "images": ["d", 0], "filename_prefix": "maestro_tile"}},
    }


_MESH_CKPT = "hunyuan_3d_v2.1.safetensors"


def build_feature_mesh_workflow(image_name: str, octree: int = 256, steps: int = 30) -> dict:
    """The local Hunyuan3D-2.1 image→mesh graph: a matted feature sprite (already isolated on
    white) becomes a .glb. The single packaged checkpoint yields MODEL + CLIP_VISION + VAE; the
    image conditions a DiT sample, the hunyuan VAE decodes a voxel field, and it's marched to a
    mesh and saved as GLB. octree_resolution trades detail for time/VRAM (256 is a fast preview,
    384 crisper)."""
    return {
        "ck": {"class_type": "ImageOnlyCheckpointLoader",
               "inputs": {"ckpt_name": _MESH_CKPT}},
        "im": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "en": {"class_type": "CLIPVisionEncode",
               "inputs": {"clip_vision": ["ck", 1], "image": ["im", 0], "crop": "center"}},
        "co": {"class_type": "Hunyuan3Dv2Conditioning",
               "inputs": {"clip_vision_output": ["en", 0]}},
        "la": {"class_type": "EmptyLatentHunyuan3Dv2",
               "inputs": {"resolution": 3072, "batch_size": 1}},
        "ks": {"class_type": "KSampler", "inputs": {
            "model": ["ck", 0], "seed": int(uuid.uuid4().int % (2**32)), "steps": steps,
            "cfg": 5.0, "sampler_name": "euler", "scheduler": "simple",
            "positive": ["co", 0], "negative": ["co", 1], "latent_image": ["la", 0],
            "denoise": 1.0}},
        "de": {"class_type": "VAEDecodeHunyuan3D", "inputs": {
            "samples": ["ks", 0], "vae": ["ck", 2], "num_chunks": 8000,
            "octree_resolution": octree}},
        "me": {"class_type": "VoxelToMeshBasic",
               "inputs": {"voxel": ["de", 0], "threshold": 0.6}},
        "sv": {"class_type": "SaveGLB",
               "inputs": {"mesh": ["me", 0], "filename_prefix": "maestro_mesh/feature"}},
    }


def mesh_enabled() -> bool:
    """Feature-mesh generation is on when a working backend is present: the trellis backend
    (venv+repo+weights on disk) or a hunyuan endpoint carrying the checkpoint. A missing/broken
    backend degrades to billboards, never a hard failure mid-build."""
    if mesh_backend() == "trellis":
        return True
    ep = _mesh_endpoint()
    if not ep:
        return False
    try:
        info = _http_get(f"{ep}/object_info/ImageOnlyCheckpointLoader")
        ckpts = info["ImageOnlyCheckpointLoader"]["input"]["required"]["ckpt_name"][0]
        return _MESH_CKPT in ckpts
    except Exception:
        return False


def _mesh_endpoint() -> str:
    cfg = _get_comfyui_settings()
    return (cfg.get("mesh_endpoint") or cfg.get("tile_endpoint") or "").rstrip("/")


def _get_trellis_settings() -> dict:
    from config.settings_manager import settings_manager
    return settings_manager.get_settings().get("trellis") or {}


def mesh_backend() -> str:
    """Which feature-mesh generator to use: 'trellis' only when it's selected AND fully
    configured (venv + repo + weights all present on disk), else 'hunyuan'. A misconfigured
    trellis silently downgrades — the build must never hard-fail on an art backend."""
    import os
    cfg = _get_comfyui_settings()
    if cfg.get("mesh_backend") == "trellis":
        t = _get_trellis_settings()
        if all(os.path.exists(t.get(k, "")) for k in ("python", "repo", "weights")):
            return "trellis"
    return "hunyuan"


def run_trellis_batch(sprite_dir: str, out_dir: str) -> set:
    """Run TRELLIS.2 over every sprite in sprite_dir → a textured .glb per slug in out_dir.
    Shells out to the standalone TRELLIS venv (loads the 4B pipeline once for the whole batch).
    Returns the set of slugs that produced a .glb. Never raises — a failure leaves billboards."""
    import json
    import os
    import subprocess
    t = _get_trellis_settings()
    runner = os.path.join(os.path.dirname(__file__), "trellis_runner.py")
    try:
        subprocess.run(
            [t["python"], runner, "--repo", t["repo"], "--weights", t["weights"],
             "--sprites", sprite_dir, "--out", out_dir],
            check=False, timeout=3600)
        tj = os.path.join(out_dir, "timings.json")
        done = set(json.load(open(tj))) if os.path.exists(tj) else set()
        return {s for s in done if os.path.exists(os.path.join(out_dir, f"{s}.glb"))}
    except Exception as e:
        logger.error(f"trellis batch failed: {e}")
        return set()


def run_mesh_job(image_path: str, dest_glb, octree: int = 256) -> bool:
    """Upload a feature sprite, run the image→mesh graph, and write the resulting .glb to
    dest_glb. Returns True on success. Endpoint is `comfyui.mesh_endpoint` (falls back to
    tile_endpoint — the ideogram box carries the 3D nodes too)."""
    import shutil
    ep = _mesh_endpoint()
    if not ep:
        return False
    try:
        name = upload_image(image_path, endpoint=ep)
        wf = build_feature_mesh_workflow(name, octree=octree)
        client_id = str(uuid.uuid4())
        resp = _http_post(f"{ep}/prompt", {"prompt": wf, "client_id": client_id})
        pid = resp.get("prompt_id")
        if not pid:
            logger.error(f"mesh job not queued: {resp}")
            return False
        result = _poll_until_done(ep, pid, timeout=600)
        for node_output in result.get("outputs", {}).values():
            for glb in node_output.get("3d", []) or node_output.get("gltf", []) \
                    or node_output.get("mesh", []):
                fn = glb["filename"] if isinstance(glb, dict) else glb
                sub = glb.get("subfolder", "") if isinstance(glb, dict) else ""
                url = (f"{ep}/view?filename={urllib.parse.quote(fn)}"
                       f"&subfolder={urllib.parse.quote(sub)}&type=output")
                data = urllib.request.urlopen(url, timeout=120).read()
                with open(dest_glb, "wb") as f:
                    f.write(data)
                return True
        logger.error(f"mesh job {pid} produced no glb output: "
                     f"{list(result.get('outputs', {}).keys())}")
        return False
    except Exception as e:
        logger.error(f"mesh job failed for {image_path}: {e}")
        return False


def build_tile_job(theme: str, role: str = "open", ideogram: bool = True) -> dict:
    """Return a job for ONE walkable-map terrain tile: a square, top-down surface TEXTURE of
    `theme` (e.g. 'frozen stream', 'temple stone'). Never say 'map tile' — models draw a picture
    OF a map. The saved image is post-processed seamless + downscaled (`make_seamless_tile`); the
    overworld repeats it per cell, so it must be a uniform material, not a scene. Renders on the
    ideogram4 endpoint (`comfyui.tile_endpoint`) when configured, else the default endpoint's
    DreamShaper formulas; `ideogram=False` forces the DreamShaper path (the caller's last
    resort when a theme phrase keeps tripping ideogram's refusal filter on every seed)."""
    if role == "blocked":
        key = "blocked_wall" if any(w in theme.lower() for w in _TILE_WALLISH) \
            else "blocked_organic"
    else:
        key = "open"
    tile_endpoint = _get_comfyui_settings().get("tile_endpoint", "").rstrip("/")
    if tile_endpoint and ideogram:
        caption = _ideogram_tile_caption(theme, key)
        return {
            "prompt": caption,
            "workflow_override": _build_ideogram_tile_workflow(caption),
            "endpoint": tile_endpoint,
        }
    positive = _TILE_FORMULAS[key].format(t=theme)
    return {
        "prompt": positive,
        "workflow_override": _build_background_workflow(
            _load_workflow(_TXT2IMG_TILE_WORKFLOW_PATH), positive, _TILE_NEGATIVE),
    }


def tile_refused(path) -> bool:
    """True when an ideogram4 tile came back as a refusal. The filtered weights don't error —
    they RENDER 'Image blocked by safety filter' as bold white text across the image center
    (sometimes over a perfectly good texture), or emit a near-transparent frame; the trigger
    is seed-dependent, the same caption passes on a reroll. Detector: near-white pixels
    concentrated in the center band with a dark-quiet remainder (a bright texture like snow
    is bright everywhere, so it doesn't trip). Third mode: a blank 'transparency' card (the
    model paints a literal checkerboard) — dead flat at low frequency where any real texture
    keeps tonal patches."""
    from PIL import Image

    img = Image.open(path)
    if "A" in img.getbands():
        alpha = img.convert("RGBA").resize((64, 64)).getchannel("A")
        if sum(a < 32 for a in alpha.getdata()) / (64 * 64) > 0.5:
            return True
    low = sorted(img.convert("L").resize((64, 64)).getdata())
    if low[int(len(low) * 0.95)] - low[int(len(low) * 0.05)] <= 3:
        return True
    g = img.convert("L").resize((256, 256))
    px = g.load()
    band = sum(1 for y in range(96, 160) for x in range(256) if px[x, y] > 215) / (64 * 256)
    rest = sum(1 for y in list(range(0, 96)) + list(range(160, 256)) for x in range(256)
               if px[x, y] > 215) / (192 * 256)
    return band > 0.01 and rest < band / 4


def make_seamless_tile(path, out_size: int = 256) -> None:
    """Make a generated texture tile-safe in place: center-square crop, wrap-shift by half so the
    hard edges land in the middle, crossfade that seam cross back to the original (which is
    continuous there), then LANCZOS-downscale to `out_size` (cells render at <=88px; 256 keeps
    headroom without shipping megapixel PNGs). Pure PIL."""
    from PIL import Image, ImageChops

    img = Image.open(path).convert("RGB")
    w, h = img.size
    s = min(w, h)
    img = img.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))

    # Two-pass cross-fade: blend the image with its own half-roll along one axis, weighting the
    # ORIGINAL by a triangle (1 at center, 0 at edges). The result's opposite edges are adjacent
    # columns/rows of the roll — continuous by construction — and the roll's own hard seam sits
    # under weight 1 of the original. Repeat for the other axis; the second pass's mask is
    # constant along the first axis, so it preserves the first wrap.
    def wrap_blend(im: Image.Image, axis: str) -> Image.Image:
        half = s // 2
        rolled = Image.new("RGB", (s, s))
        if axis == "x":
            rolled.paste(im.crop((half, 0, s, s)), (0, 0))
            rolled.paste(im.crop((0, 0, half, s)), (s - half, 0))
        else:
            rolled.paste(im.crop((0, half, s, s)), (0, 0))
            rolled.paste(im.crop((0, 0, s, half)), (0, s - half))
        # Trapezoid mask: original at full weight everywhere except a thin fade band at the two
        # edges (~s/12) — the blend ghosting stays confined to the border instead of doubling
        # features across the whole tile.
        grad = Image.linear_gradient("L").resize((s, s))   # 0 at top -> 255 at bottom
        tent = ImageChops.darker(grad, grad.transpose(Image.FLIP_TOP_BOTTOM)) \
            .point(lambda v: min(255, v * 12))
        mask = tent if axis == "y" else tent.transpose(Image.ROTATE_90)
        return Image.composite(im, rolled, mask)

    out = wrap_blend(wrap_blend(img, "x"), "y")
    out.resize((out_size, out_size), Image.LANCZOS).save(path)


def build_cg_job(description: str) -> dict:
    """Return a {prompt, workflow_override} job dict for a full-screen CG illustration.
    Renders on WAI Illustrious (like backgrounds): the Anima/Qwen model letterboxes 1280x720
    with baked black bars, and Illustrious keeps CGs in the same visual family as the scenes
    they interrupt."""
    positive = (f"{description} — anime visual novel CG illustration, characters in their "
                f"environment, dynamic composition, dramatic lighting, {_BG_QUALITY}")
    return {
        "prompt": positive,
        "workflow_override": _build_background_workflow(
            _load_workflow(_TXT2IMG_BACKGROUND_WORKFLOW_PATH), positive, _CG_NEGATIVE),
    }


def build_title_card_job(description: str) -> dict:
    """Return a {prompt, workflow_override} job dict for the game title card. WAI Illustrious
    widescreen, same reasoning as build_cg_job (observed: Anima baked letterbox bars into the
    1280x720 title image)."""
    positive = (f"{description} — cinematic visual novel title key visual, atmospheric "
                f"composition, {_BG_QUALITY}")
    return {
        "prompt": positive,
        "workflow_override": _build_background_workflow(
            _load_workflow(_TXT2IMG_BACKGROUND_WORKFLOW_PATH), positive, _TITLE_CARD_NEGATIVE),
    }


def _load_workflow(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_comfyui_settings() -> dict:
    from config.settings_manager import settings_manager
    settings = settings_manager.get_settings()
    return settings.get("comfyui", {})


def _get_comfyui_endpoint() -> str:
    return _get_comfyui_settings().get("endpoint", "http://localhost:8188").rstrip("/")


def _get_lmstudio_base_url() -> str:
    from config.settings_manager import settings_manager
    settings = settings_manager.get_settings()
    return settings.get("lmstudio", {}).get("base_url", "http://localhost:1234").rstrip("/")


def _http_post(url: str, data: dict) -> dict:
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
        return json.loads(body) if body else {}


def _http_get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


def _poll_until_done(endpoint: str, prompt_id: str, timeout: int = 300) -> dict:
    """Poll /history/{prompt_id} until job appears (ComfyUI removes it from queue when done)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        history = _http_get(f"{endpoint}/history/{prompt_id}")
        if prompt_id in history:
            return history[prompt_id]
        time.sleep(1)
    raise TimeoutError(f"ComfyUI job {prompt_id} did not complete within {timeout}s")


def _lmstudio_get_loaded_model() -> Optional[str]:
    """Return identifier of first loaded model in LM Studio, or None."""
    try:
        base_url = _get_lmstudio_base_url()
        data = _http_get(f"{base_url}/api/v0/models")
        models = data.get("data", [])
        for m in models:
            if m.get("state") == "loaded":
                return m.get("id")
        return None
    except Exception as e:
        logger.warning(f"Could not query LM Studio models: {e}")
        return None


def _lmstudio_unload(model_id: str, wait_timeout: int = 30) -> bool:
    """Unload a model from LM Studio and wait until it is no longer loaded."""
    try:
        base_url = _get_lmstudio_base_url()
        _http_post(f"{base_url}/api/v1/models/unload", {"instance_id": model_id})
        logger.info(f"LM Studio: unload requested for {model_id}, waiting for VRAM release...")
    except Exception as e:
        logger.warning(f"LM Studio unload failed: {e}")
        return False

    # Poll until model is no longer in loaded state
    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        try:
            base_url = _get_lmstudio_base_url()
            data = _http_get(f"{base_url}/api/v0/models")
            loaded_ids = {m.get("id") for m in data.get("data", []) if m.get("state") == "loaded"}
            if model_id not in loaded_ids:
                logger.info(f"LM Studio: {model_id} confirmed unloaded")
                return True
        except Exception:
            pass
        time.sleep(1)

    logger.warning(f"LM Studio: {model_id} did not confirm unload within {wait_timeout}s — proceeding anyway")
    return False


def _lmstudio_load(model_id: str) -> bool:
    """Load a model into LM Studio. Returns True on success."""
    try:
        base_url = _get_lmstudio_base_url()
        _http_post(f"{base_url}/api/v1/models/load", {"model": model_id})
        logger.info(f"LM Studio: loaded {model_id}")
        return True
    except Exception as e:
        logger.warning(f"LM Studio load failed: {e}")
        return False


def _llamacpp_get_loaded_model() -> Optional[str]:
    """Return the id of the first model the llama.cpp router reports as loaded, or None."""
    try:
        base_url = _get_lmstudio_base_url()
        data = _http_get(f"{base_url}/models")
        for m in data.get("data", []):
            if m.get("status", {}).get("value") == "loaded":
                return m.get("id")
        return None
    except Exception as e:
        logger.warning(f"Could not query llama.cpp models: {e}")
        return None


def _llamacpp_unload(model_id: str, wait_timeout: int = 30) -> bool:
    """Unload a model from the llama.cpp router and wait until it reports unloaded."""
    try:
        base_url = _get_lmstudio_base_url()
        _http_post(f"{base_url}/models/unload", {"model": model_id})
        logger.info(f"llama.cpp: unload requested for {model_id}, waiting for VRAM release...")
    except Exception as e:
        logger.warning(f"llama.cpp unload failed: {e}")
        return False

    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        try:
            base_url = _get_lmstudio_base_url()
            data = _http_get(f"{base_url}/models")
            for m in data.get("data", []):
                if m.get("id") == model_id and m.get("status", {}).get("value") == "unloaded":
                    logger.info(f"llama.cpp: {model_id} confirmed unloaded")
                    return True
        except Exception:
            pass
        time.sleep(1)

    logger.warning(f"llama.cpp: {model_id} did not confirm unload within {wait_timeout}s — proceeding anyway")
    return False


def _llamacpp_load(model_id: str) -> bool:
    """Load a model into the llama.cpp router. Returns True on success."""
    try:
        base_url = _get_lmstudio_base_url()
        _http_post(f"{base_url}/models/load", {"model": model_id})
        logger.info(f"llama.cpp: loaded {model_id}")
        return True
    except Exception as e:
        logger.warning(f"llama.cpp load failed: {e}")
        return False


# The LLM server flavor is auto-detected from the endpoint, never configured: `connector_type`
# stays the OpenAI-compatible label while the actual server is either LM Studio (native /api/v0
# REST) or a llama.cpp router (/models with per-model status). Whichever route answers wins; a
# plain single-model llama-server answers neither -> no VRAM management (its model is pinned for
# the process lifetime, so the LLM simply stays resident alongside the image model).
_llm_flavor: Optional[str] = None


def _detect_llm_flavor() -> str:
    global _llm_flavor
    if _llm_flavor is not None:
        return _llm_flavor
    base_url = _get_lmstudio_base_url()
    try:
        _http_get(f"{base_url}/api/v0/models")
        _llm_flavor = "lmstudio"
        return _llm_flavor
    except Exception:
        pass
    try:
        data = _http_get(f"{base_url}/models")
        if any("status" in (m or {}) for m in data.get("data", [])):
            _llm_flavor = "llamacpp"
            return _llm_flavor
    except Exception:
        pass
    return "none"  # not cached: re-probe next call (server may not be up yet)


def reset_llm_flavor_cache() -> None:
    """Drop the cached LLM-server flavor. Call after the LLM endpoint changes."""
    global _llm_flavor
    _llm_flavor = None


def _llm_get_loaded_model() -> Optional[str]:
    flavor = _detect_llm_flavor()
    if flavor == "lmstudio":
        return _lmstudio_get_loaded_model()
    if flavor == "llamacpp":
        return _llamacpp_get_loaded_model()
    return None


def _llm_unload(model_id: str) -> bool:
    flavor = _detect_llm_flavor()
    if flavor == "lmstudio":
        return _lmstudio_unload(model_id)
    if flavor == "llamacpp":
        return _llamacpp_unload(model_id)
    return False


def _llm_load(model_id: str) -> bool:
    flavor = _detect_llm_flavor()
    if flavor == "lmstudio":
        return _lmstudio_load(model_id)
    if flavor == "llamacpp":
        return _llamacpp_load(model_id)
    return False


def _comfyui_free_vram(endpoint: str, timeout: int = 60) -> None:
    """Ask ComfyUI to release models from VRAM, then poll until VRAM usage stabilizes."""
    try:
        _http_post(f"{endpoint}/free", {"unload_models": True, "free_memory": True})
    except Exception as e:
        logger.warning(f"ComfyUI /free failed: {e}")
        return

    # Poll /system_stats until torch_vram_free stabilizes (two consecutive equal readings)
    deadline = time.time() + timeout
    prev_free = None
    while time.time() < deadline:
        time.sleep(2)
        try:
            stats = _http_get(f"{endpoint}/system_stats")
            devices = stats.get("devices", [])
            if not devices:
                break
            vram_free = devices[0].get("torch_vram_free", 0)
            if prev_free is not None and vram_free == prev_free:
                logger.info(f"ComfyUI VRAM stabilized: {vram_free // 1024 // 1024} MiB free")
                return
            prev_free = vram_free
        except Exception:
            break

    logger.warning("ComfyUI VRAM poll timed out or failed, proceeding anyway")


def _save_images_to_working_dir(images: list, endpoint: str) -> list:
    """Download ComfyUI output images into the task working directory. Returns list of saved absolute paths."""
    from tools.execution_context import resolve_base_path
    import shutil

    saved = []
    base_dir = resolve_base_path()
    base_dir.mkdir(parents=True, exist_ok=True)

    for img in images:
        url = img["url"]
        filename = img["filename"]
        dest = base_dir / filename
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                with open(dest, "wb") as f:
                    shutil.copyfileobj(resp, f)
            saved.append(str(dest))
            logger.info(f"Saved image: {dest}")
        except Exception as e:
            logger.warning(f"Failed to save image {filename}: {e}")

    return saved


def _run_comfyui_job(endpoint: str, prompt: str, workflow_override: Optional[dict]) -> Dict[str, Any]:
    """Submit one job to ComfyUI and wait for result. No VRAM management."""
    if workflow_override is not None:
        workflow = workflow_override
    else:
        workflow = _load_workflow(_TXT2IMG_WORKFLOW_PATH)
        workflow["11"]["inputs"]["text"] = prompt
        workflow["19"]["inputs"]["seed"] = int(uuid.uuid4().int % (2**32))

    client_id = str(uuid.uuid4())
    queue_resp = _http_post(f"{endpoint}/prompt", {"prompt": workflow, "client_id": client_id})
    prompt_id = queue_resp.get("prompt_id")
    if not prompt_id:
        return {"success": False, "error": f"No prompt_id in response: {queue_resp}"}

    logger.info(f"ComfyUI job queued: {prompt_id}")
    result = _poll_until_done(endpoint, prompt_id)

    outputs = result.get("outputs", {})
    images = []
    for node_output in outputs.values():
        for img in node_output.get("images", []):
            images.append({
                "filename": img["filename"],
                "subfolder": img.get("subfolder", ""),
                "type": img.get("type", "output"),
                "url": (
                    f"{endpoint}/view"
                    f"?filename={urllib.parse.quote(img['filename'])}"
                    f"&subfolder={urllib.parse.quote(img.get('subfolder', ''))}"
                    f"&type={img.get('type', 'output')}"
                ),
            })

    saved_paths = _save_images_to_working_dir(images, endpoint)
    saved_str = ", ".join(saved_paths) if saved_paths else "(none saved)"
    return {
        "success": True,
        "prompt_id": prompt_id,
        "prompt": prompt,
        "images": images,
        "image_count": len(images),
        "saved_paths": saved_paths,
        "message": f"Image generated successfully. Saved to: {saved_str}",
    }


@contextmanager
def vram_bracket():
    """Free ComfyUI/LM-Studio VRAM for the duration, reloading the LLM on exit. Lets a caller
    run several `run_jobs` passes (e.g. neutral bases then img2img emotion variants) inside ONE
    unload/reload cycle instead of paying it per pass. No-op unless `comfyui.vram_management`."""
    cfg = _get_comfyui_settings()
    vram_management = cfg.get("vram_management", False)
    endpoints = [_get_comfyui_endpoint()]
    if cfg.get("tile_endpoint", "").rstrip("/"):
        endpoints.append(cfg["tile_endpoint"].rstrip("/"))
    unloaded_model: Optional[str] = None
    if vram_management:
        for ep in endpoints:
            _comfyui_free_vram(ep)
        unloaded_model = _llm_get_loaded_model()
        if unloaded_model:
            _llm_unload(unloaded_model)
    try:
        yield
    finally:
        if vram_management:
            for ep in endpoints:
                _comfyui_free_vram(ep)
            if unloaded_model:
                _llm_load(unloaded_model)


def run_jobs(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Run image jobs sequentially (no VRAM management — wrap in `vram_bracket`). Each job:
    {"prompt": str, "workflow_override": dict | None, "endpoint": str | absent} — a job
    carrying its own endpoint (ideogram4 tiles) renders there instead of the default."""
    endpoint = _get_comfyui_endpoint()
    results = []
    for job in jobs:
        try:
            result = _run_comfyui_job(job.get("endpoint") or endpoint, job["prompt"],
                                      job.get("workflow_override"))
        except Exception as e:
            logger.error(f"run_jobs job failed: {e}")
            result = {"success": False, "error": str(e)}
        results.append(result)
    return results


@tool_manager.tool(
    description="Generate an image from a text prompt using ComfyUI. Returns URLs and filenames for the generated image(s). Use this when asked to create, draw, or visualize anything.",
    auto_inject_context=False,
)
def generate_image(prompt: str, workflow_override: Optional[dict] = None) -> Dict[str, Any]:
    comfyui_settings = _get_comfyui_settings()
    vram_management  = comfyui_settings.get("vram_management", False)
    endpoint         = _get_comfyui_endpoint()

    unloaded_model: Optional[str] = None
    try:
        if vram_management:
            _comfyui_free_vram(endpoint)
            unloaded_model = _llm_get_loaded_model()
            if unloaded_model:
                _llm_unload(unloaded_model)

        result = _run_comfyui_job(endpoint, prompt, workflow_override)

        if vram_management:
            _comfyui_free_vram(endpoint)
            if unloaded_model:
                _llm_load(unloaded_model)

        return result

    except urllib.error.URLError as e:
        if vram_management and unloaded_model:
            _llm_load(unloaded_model)
        return {"success": False, "error": f"Cannot reach ComfyUI at {endpoint}: {e.reason}. Is ComfyUI running with --listen?"}
    except TimeoutError as e:
        if vram_management and unloaded_model:
            _llm_load(unloaded_model)
        return {"success": False, "error": str(e)}
    except Exception as e:
        if vram_management and unloaded_model:
            _llm_load(unloaded_model)
        logger.error(f"generate_image failed: {e}")
        return {"success": False, "error": str(e)}


