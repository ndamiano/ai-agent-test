"""The spec's CONTROL VOCABULARY — normalizing what a spec says the player presses into keys
that actually exist.

The runtime's whole input surface is `down(key)` / `pressed(key)` / `pointer` (+ mouse-look
deltas): keys are single lowercase characters, `Arrow*`, `Escape`/`Tab`/`Shift`/`Enter` — there is
no gamepad and the mouse is not a key. But a spec asking for `A_BUTTON: swing weapon` or
`Right Click: parry` has stated a perfectly clear intent, so the pipeline MAPS it rather than
refusing it. An unmapped control leaves the scaffold with nothing to bind, which no edit
downstream can satisfy — and `input.pressed("Right Click")` is never true, so the control ships
dead.
"""

import re


# A gamepad control → the keyboard/mouse equivalent that actually exists. The runtime has no gamepad
# (Input is keys + pointer), but "A_BUTTON: swing weapon" is perfectly clear intent — so MAP it
# rather than refuse it.
_PAD_ACTION = {"a": "e", "b": "q", "x": "f", "y": "r",
               "cross": "e", "circle": "q", "square": "f", "triangle": "r",
               "lb": "1", "l1": "1", "rb": "2", "r1": "2",
               "lt": "Shift", "l2": "Shift", "rt": " ", "r2": " ",
               "start": "Escape", "menu": "Escape", "select": "Tab", "back": "Tab"}
_PAD_SPARE = ["e", "q", "f", "r", "c", "1", "2", "3", "Shift", "Tab", " "]

_MOVEMENT = "W/A/S/D"     # the scaffold owns movement; it is never a per-key binding
_MOUSE = "Mouse"
_MOUSE_SCHEMES = {"first-person-3d", "orbital-3d"}   # the only schemes whose runtime has a mouse

_NAMED = {"Escape", "Tab", "Shift", "Enter", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"}
_ARROW_WORD = {"up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight",
               "uparrow": "ArrowUp", "downarrow": "ArrowDown",
               "leftarrow": "ArrowLeft", "rightarrow": "ArrowRight",
               "arrowup": "ArrowUp", "arrowdown": "ArrowDown",
               "arrowleft": "ArrowLeft", "arrowright": "ArrowRight",
               "upkey": "ArrowUp", "downkey": "ArrowDown",
               "leftkey": "ArrowLeft", "rightkey": "ArrowRight"}
_WORD_KEY = {"space": " ", "spacebar": " ", "spacekey": " ",
             "esc": "Escape", "escape": "Escape",
             "enter": "Enter", "return": "Enter",
             "tab": "Tab", "shift": "Shift", "lshift": "Shift", "rshift": "Shift",
             **_ARROW_WORD}

_MOVE_GROUP = {"wasd", "arrow", "arrows", "arrowkeys", "arrowkey", "cursorkeys", "dpad"}
_MOVE_TOKEN = _MOVE_GROUP | set(_ARROW_WORD) | {"w", "a", "s", "d"}

_MOUSE_RE = re.compile(r"mouse|click|pointer|cursor|drag|wheel|scroll|lmb|rmb|mmb")
_CLICK_RE = re.compile(r"click|button|lmb|rmb|mmb|(left|right|middle)[\s_-]*mouse")
_MOUSE_AXIS_RE = re.compile(r"mouse(x|y|xy|yx|axis|axes)?")
_ALT_SPLIT = re.compile(r"\s*(?:/|,|\||\+|\bor\b)\s*", re.I)
_DIGIT_RANGE = re.compile(r"\s*(\d)\s*[-–]\s*\d\s*$")


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _key_for_pad(raw: str) -> str | None:
    """The key a gamepad-shaped control name means, or None if it isn't gamepad-shaped."""
    if len(raw.strip()) == 1:
        return None                # a single character is already a real key, never a pad button
    t = _alnum(raw)
    if re.fullmatch(r"(l|left)stick|d?pad|directionalpad", t):
        return _MOVEMENT          # the steering stick IS the movement keys
    if re.fullmatch(r"(r|right)stick", t):
        return _MOUSE             # the look stick IS the mouse — both are the camera
    return _PAD_ACTION.get(re.sub(r"(button|btn|trigger|bumper)$", "", t))


def _alts(raw: str) -> list:
    return [p for p in (p.strip() for p in _ALT_SPLIT.split(raw)) if p]


def _is_movement(raw: str) -> bool:
    toks = [_alnum(p) for p in _alts(raw)]
    if not toks or any(t not in _MOVE_TOKEN for t in toks):
        return False
    return len(toks) > 1 or toks[0] in _MOVE_GROUP


def _key_for_token(tok: str) -> str | None:
    """The real key one alternative names, or None if it names none (a mouse, an unknown word)."""
    if tok in _NAMED or tok == " ":
        return tok
    t = re.sub(r"^(?:the\s+|key\s+)", "", tok.strip(), flags=re.I)
    a = _alnum(t)
    if a in _WORD_KEY:
        return _WORD_KEY[a]
    if len(a) == 1:
        return a
    m = _DIGIT_RANGE.match(t)
    return m.group(1) if m else None


def _resolve(raw: str, scheme: str | None) -> tuple:
    """(key, movable) — the key this control name means. `movable` marks a key the caller may move
    to a free one on collision (a pad alias, a mouse action, a mouse click)."""
    pad = _key_for_pad(raw)
    if pad is not None:
        return pad, pad not in (_MOVEMENT, _MOUSE)
    if _is_movement(raw):
        return _MOVEMENT, False
    if not _MOUSE_AXIS_RE.fullmatch(_alnum(raw)):     # "MOUSE_X/Y" is one axis pair, not a "Y" key
        for alt in _alts(raw):
            key = _key_for_token(alt)
            if key is not None:
                return key, False
    low = raw.lower()
    if _MOUSE_RE.search(low):
        # Only a mouse-owning scheme has a mouse; everywhere else the action must land on a key.
        if scheme not in _MOUSE_SCHEMES:
            return None, True
        # There is ONE pointer, so a second mouse action can't be told from the first — the look
        # half keeps it and a click yields, rather than overwriting the entry and losing a mechanic.
        return _MOUSE, bool(_CLICK_RE.search(low))
    return raw, False


def normalize_controls(design: dict) -> None:
    """Rewrite the spec's `controls` map onto keys the runtime can bind, in place.

    Done HERE, at the spec boundary, so the human reviewing the spec, the authoring prompt, the
    scaffold and the prompts all read the SAME key. Aliasing at one site alone would have it
    demand a binding on "E" while the model, reading "A_BUTTON" off the spec, binds that — the
    ping-pong between a spec key and a key that exists.
    """
    controls = design.get("controls")
    if not isinstance(controls, dict):
        return
    control = design.get("control")
    scheme = control.get("scheme") if isinstance(control, dict) else None

    resolved = [(what, *_resolve(str(raw), scheme)) for raw, what in controls.items()]
    taken = {key for _, key, movable in resolved if key is not None and not movable}
    out = {}
    for what, key, movable in resolved:
        if key is None or (movable and key in taken):
            key = next((k for k in _PAD_SPARE if k not in taken), key or _MOUSE)
        taken.add(key)
        out[key] = what
    design["controls"] = out
