"""The spec's CONTROL VOCABULARY — normalizing what a spec says the player presses into keys
that actually exist.

The runtime's whole input surface is `down(key)` / `pressed(key)` / `pointer` (+ mouse-look
deltas): there is no gamepad. But a spec asking for `A_BUTTON: swing weapon` has stated a
perfectly clear intent, so the pipeline MAPS it rather than refusing it — the same way the probe
already folds `LEFT_CLICK`/`Spacebar`/`Up` into the tokens it understands. Refusing instead of
mapping cost one measured build its entire step budget: `unbound_control` demanded a binding on
`LEFT_STICK`, which no edit can ever satisfy.
"""

import re


# A gamepad control → the keyboard/mouse equivalent that actually exists. The runtime has no gamepad
# (Input is keys + pointer), but "A_BUTTON: swing weapon" is perfectly clear intent — so MAP it
# rather than refuse it, the same way the probe already normalizes "LEFT_CLICK"/"Spacebar"/"Up".
_PAD_ACTION = {"a": "E", "b": "Q", "x": "F", "y": "R",
               "cross": "E", "circle": "Q", "square": "F", "triangle": "R",
               "lb": "1", "l1": "1", "rb": "2", "r1": "2",
               "lt": "Shift", "l2": "Shift", "rt": " ", "r2": " ",
               "start": "Escape", "menu": "Escape", "select": "Tab", "back": "Tab"}
_PAD_SPARE = ["E", "Q", "F", "R", "C", "1", "2", "3", "Shift", "Tab", " "]


def _key_for_pad(raw: str, taken: set) -> str | None:
    """The key a gamepad-shaped control name means, or None if it isn't gamepad-shaped."""
    t = re.sub(r"[^a-z0-9]", "", raw.lower())
    if re.fullmatch(r"(l|left)stick|d?pad|directionalpad", t):
        return "W/A/S/D"          # the steering stick IS the movement keys
    if re.fullmatch(r"(r|right)stick", t):
        return "Mouse"            # the look stick IS the mouse — both are the camera
    key = _PAD_ACTION.get(re.sub(r"(button|btn|trigger|bumper)$", "", t))
    if key is None:
        return None
    return key if key not in taken else next((k for k in _PAD_SPARE if k not in taken), key)


def normalize_controls(design: dict) -> None:
    """Rewrite gamepad control names in the spec's `controls` map to real keys, in place.

    Done HERE, at the spec boundary, so the human reviewing the spec, the authoring prompt, the
    scaffold and the probe all read the SAME key. Aliasing inside the probe alone would have it
    demand a binding on "E" while the model, reading "A_BUTTON" off the spec, binds that — the
    ping-pong the probe's own MOUSE-token comment warns about.
    """
    controls = design.get("controls")
    if not isinstance(controls, dict):
        return
    taken = {str(k) for k in controls if _key_for_pad(str(k), set()) is None}
    out = {}
    for raw, what in controls.items():
        key = _key_for_pad(str(raw), taken)
        if key is None:
            key = str(raw)
        else:
            taken.add(key)
        out[key] = what
    design["controls"] = out
