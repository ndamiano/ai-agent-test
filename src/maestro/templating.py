"""Prompt templating: {{include:NAME}} partials + {key} substitution.

The codegen prompts are static (read verbatim); the Module ABC keeps this for any future module that
wants a templated correction prompt.
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

_INCLUDE_RE = re.compile(r"\{\{include:\s*([A-Za-z0-9_]+)\s*\}\}")


def _resolve_includes(text: str, partials_dir: Path, _seen=None) -> str:
    _seen = _seen or set()

    def replace(match):
        name = match.group(1)
        if name in _seen:
            raise ValueError(f"circular prompt include: {name}")
        path = partials_dir / f"{name}.txt"
        if not path.exists():
            raise FileNotFoundError(f"Prompt partial not found: {path}")
        return _resolve_includes(path.read_text(encoding="utf-8"), partials_dir, _seen | {name})

    return _INCLUDE_RE.sub(replace, text)


def render_template(template_path: Path, inputs: Dict) -> str:
    if not template_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {template_path}")

    template_str = _resolve_includes(template_path.read_text(encoding="utf-8"),
                                     template_path.parent / "partials")

    def replace(match):
        key = match.group(1).strip()
        as_json = key.endswith("|json")
        if as_json:
            key = key[:-5].strip()
        if key not in inputs:
            logger.warning("render_template: missing key %r in template %s", key, template_path.name)
            return "" if not as_json else "null"
        value = inputs[key]
        if as_json or isinstance(value, (dict, list)):
            return json.dumps(value, indent=2)
        return str(value)

    return re.sub(r"\{([A-Za-z_][A-Za-z0-9_|]*)\}", replace, template_str)
