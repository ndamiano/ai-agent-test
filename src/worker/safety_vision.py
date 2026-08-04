"""Post-render NSFW verdict for the image queue.

The worker only ever REPORTS scores — every image handler result carries them, and the control
plane's policy decides what a score means (`maestro/codegen/assets.py:render_verdict`). Keeping
policy out of here is what lets a future surface with different rules reuse the same verdicts.

The model is a small timm ViT loaded from `SAFETY_MODEL_DIR` (a directory holding `model.pt` +
`config.json`, produced by `scripts/export_safety_model.py`) — local files only, since pods never
talk to Hugging Face after provisioning. A classifier that cannot load reports its error in place
of scores; the control plane refuses saves without scores, so the failure is closed, not silent.
"""

import io
import json
import logging
import os
import threading
from pathlib import Path
from typing import Dict

logger = logging.getLogger("worker")

_lock = threading.Lock()
_loaded = None  # (model, cfg, device, transform) | Exception


def _load():
    import timm
    import torch

    model_dir = Path(os.environ.get("SAFETY_MODEL_DIR", "/opt/comfy-models/safety"))
    cfg = json.loads((model_dir / "config.json").read_text())
    model = timm.create_model(cfg["architecture"], num_classes=cfg["num_classes"],
                              pretrained=False)
    state = torch.load(model_dir / "model.pt", map_location="cpu", weights_only=True)
    model.load_state_dict(state)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.eval().to(device)
    transform = timm.data.create_transform(**cfg["data_config"], is_training=False)
    return model, cfg, device, transform


def classify(image_bytes: bytes) -> Dict:
    """{"scores": {label: probability}} for one rendered image, or {"error": ...}."""
    global _loaded
    with _lock:
        if _loaded is None:
            try:
                _loaded = _load()
            except Exception as e:
                logger.exception("safety classifier failed to load")
                _loaded = e
        if isinstance(_loaded, Exception):
            return {"error": f"classifier unavailable: {_loaded}"}
        model, cfg, device, transform = _loaded
        try:
            return {"scores": _scores(model, cfg, device, transform, image_bytes)}
        except Exception as e:
            logger.exception("safety classify failed")
            return {"error": str(e)}


def _scores(model, cfg: Dict, device: str, transform, image_bytes: bytes) -> Dict[str, float]:
    import torch
    from PIL import Image

    im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    with torch.inference_mode():
        probs = model(transform(im).unsqueeze(0).to(device)).softmax(dim=-1)[0].cpu()
    return {label: round(float(p), 4) for label, p in zip(cfg["label_names"], probs)}
