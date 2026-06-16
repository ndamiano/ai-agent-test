"""Durable on-disk state for one game build.

Each game gets its own run directory under <working_directory>/runs/<run_id>/.
Inside it:
  - <component_id>.json   one file per artifact component (premise, node_scripts, ...)
  - spec.json             the frozen contract
  - scratchpad.json       structured working memory (replaced, never appended)
  - story_state.json      continuity bible (Phase 6)

Component ids map directly to the filenames compile_renpy reads (premise.json,
asset_manifest.json, node_scripts.json, brief.json), so the run dir IS a valid
compile working dir.

This is the source of truth. The agent's transcript is not used as memory; each
step rebuilds context from here.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

SPEC_FILE = "spec.json"
SCRATCHPAD_FILE = "scratchpad.json"
STORY_STATE_FILE = "story_state.json"

# Files that live in the run dir but are NOT artifact components.
_RESERVED = {SPEC_FILE, SCRATCHPAD_FILE, STORY_STATE_FILE}

_SCRATCHPAD_FIELDS = ("current_goal", "recent_decisions", "open_questions")


class RunState:
    def __init__(self, run_dir):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_run(cls, run_id: str) -> "RunState":
        from tools.execution_context import resolve_base_path
        return cls(resolve_base_path() / "runs" / run_id)

    # ── components ──────────────────────────────────────────────────────────
    def write_component(self, component_id: str, content) -> None:
        if f"{component_id}.json" in _RESERVED:
            raise ValueError(f"'{component_id}' is a reserved name, not a component id")
        self._write(f"{component_id}.json", content)

    def read_component(self, component_id: str):
        return self._read(f"{component_id}.json")

    def load_artifact(self) -> Dict:
        """Merged {component_id: content} for every component file in the run dir."""
        artifact: Dict = {}
        for path in sorted(self.run_dir.glob("*.json")):
            if path.name in _RESERVED:
                continue
            artifact[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        return artifact

    def component_ids(self) -> List[str]:
        return [p.stem for p in sorted(self.run_dir.glob("*.json")) if p.name not in _RESERVED]

    # ── spec ────────────────────────────────────────────────────────────────
    def write_spec(self, spec: Dict) -> None:
        self._write(SPEC_FILE, spec)

    def read_spec(self) -> Optional[Dict]:
        return self._read(SPEC_FILE)

    # ── scratchpad (structured, replace-not-append) ──────────────────────────
    def write_scratchpad(self, current_goal: str = "",
                         recent_decisions: Optional[List[str]] = None,
                         open_questions: Optional[List[str]] = None) -> None:
        self._write(SCRATCHPAD_FILE, {
            "current_goal": current_goal,
            "recent_decisions": recent_decisions or [],
            "open_questions": open_questions or [],
        })

    def read_scratchpad(self) -> Dict:
        data = self._read(SCRATCHPAD_FILE) or {}
        return {f: data.get(f, "" if f == "current_goal" else []) for f in _SCRATCHPAD_FIELDS}

    # ── story state (Phase 6) ─────────────────────────────────────────────────
    def write_story_state(self, state: Dict) -> None:
        self._write(STORY_STATE_FILE, state)

    def read_story_state(self) -> Optional[Dict]:
        return self._read(STORY_STATE_FILE)

    # ── io ────────────────────────────────────────────────────────────────────
    def _write(self, filename: str, data) -> None:
        (self.run_dir / filename).write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _read(self, filename: str):
        path = self.run_dir / filename
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
