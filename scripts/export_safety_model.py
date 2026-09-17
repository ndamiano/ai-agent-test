import json
import sys
from pathlib import Path

MODEL_ID = "hf_hub:Marqo/nsfw-image-detection-384"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)

    import timm
    import torch

    model = timm.create_model(MODEL_ID, pretrained=True)
    # The full resolved data config, so the worker feeds timm.data.create_transform verbatim —
    # a hand-rolled resize was measured 0.15 off the official pipeline's probabilities.
    data_cfg = {k: list(v) if isinstance(v, tuple) else v
                for k, v in timm.data.resolve_model_data_config(model).items()}
    cfg = {
        "architecture": model.pretrained_cfg["architecture"],
        "num_classes": model.num_classes,
        "label_names": list(model.pretrained_cfg["label_names"]),
        "data_config": data_cfg,
    }
    torch.save(model.state_dict(), out / "model.pt")
    (out / "config.json").write_text(json.dumps(cfg, indent=2))
    print(f"exported {cfg['architecture']} ({cfg['label_names']}) to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
