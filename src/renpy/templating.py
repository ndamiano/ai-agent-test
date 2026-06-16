import json
import logging
import re
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)


def render_template(template_path: Path, inputs: Dict) -> str:
    if not template_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {template_path}")

    template_str = template_path.read_text(encoding="utf-8")

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
