"""The motion library, as embeddings rather than a language model.

Kimodo is text-to-motion: it turns a sentence into motion via LLM2Vec embeddings of Llama-3, a
gated 15 GB model under Meta's licence. But the model only ever asks its encoder for a 4096-wide
vector per prompt, and for a FIXED library that vector never changes — so it is computed once, off
the pod, and shipped as a table. Prod loads 184 KB and no language model at all.

`bake` is the offline half and needs the real encoder; `Table` is what the worker uses.
"""
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

TABLE_PATH = Path(__file__).with_name("verb_embeddings.npz")

# One sentence per verb. The prose matters: a description of a WHOLE-BODY action gives motion,
# where a description of a mood gives a figure standing still (measured: "breathing calmly" moved
# 2 degrees, "staggers backward and collapses" moved 154).
VERBS: Dict[str, str] = {
    "walk": "a person walks forward at a steady pace",
    "run": "a person runs forward quickly",
    "idle": "a person stands still, breathing calmly, shifting weight slightly from foot to foot",
    "attack": "a warrior swings a sword downward in a powerful overhead attack, then returns to a "
              "fighting stance",
    "thrust": "a warrior lunges forward with a spear thrust, then steps back into a guard",
    "cast": "a wizard raises both arms and casts a spell forward, then lowers their arms",
    "hurt": "a person flinches backward as they are hit, clutching their chest, then recovers",
    "death": "a person is struck, staggers backward and collapses to the ground, lying still",
    "dodge": "a person dives sideways into a quick roll and comes back up onto their feet",
    "jump": "a person crouches and jumps straight up, landing on both feet",
    "block": "a person raises a shield in front of them and braces against a blow",
    "cheer": "a person raises both fists and celebrates",
}


def key(text: str) -> str:
    """The lookup key. Kimodo capitalises a prompt and appends a period before encoding, so a
    table keyed on the raw string misses its own entries."""
    return str(text).strip().rstrip(".").strip().lower()


class Table:
    """The text encoder the worker ships: `encoder(texts) -> (feat, lengths)`, by lookup."""

    def __init__(self, path=TABLE_PATH, device=None, dtype=None):
        data = np.load(str(path), allow_pickle=True)
        self.names = [str(n) for n in data["names"]]
        self.prompts = [str(p) for p in data["prompts"]]
        self.feats = data["feats"]
        self.lengths = [int(x) for x in data["lengths"]]
        self._index = {}
        for i, (n, p) in enumerate(zip(self.names, self.prompts)):
            self._index[key(n)] = i
            self._index[key(p)] = i
        self.llm_dim = int(self.feats.shape[-1])
        self.device = device or "cpu"
        self.dtype = dtype

    def to(self, device=None, dtype=None):
        if device is not None:
            self.device = device
        if dtype is not None:
            self.dtype = dtype
        return self

    def resolve(self, text: str) -> str:
        """The verb a piece of prose names, or "" when the library has nothing for it."""
        k = key(text)
        return self.names[self._index[k]] if k in self._index else ""

    def prompt_for(self, verb: str) -> str:
        return self.prompts[self._index[key(verb)]]

    def __call__(self, texts) -> Tuple["object", List[int]]:
        import torch
        if isinstance(texts, str):
            texts = [texts]
        idx = []
        for t in texts:
            k = key(t)
            if k not in self._index:
                raise KeyError(f"no baked embedding for {t!r}; library: {sorted(set(self.names))}")
            idx.append(self._index[k])
        feat = torch.tensor(self.feats[idx], device=self.device,
                            dtype=self.dtype or torch.float32)
        return feat, [self.lengths[i] for i in idx]


def bake(out_path, encoder, verbs: Dict[str, str] = None) -> Path:
    """Compute the table. Runs off the pod, with the real LLM2Vec encoder."""
    verbs = verbs or VERBS
    names = list(verbs)
    feats, lengths = encoder([verbs[n] for n in names])
    feats = feats.detach().cpu().numpy().astype(np.float32)
    np.savez_compressed(str(out_path), names=np.array(names),
                        prompts=np.array([verbs[n] for n in names]),
                        feats=feats, lengths=np.array(lengths))
    return Path(out_path)
