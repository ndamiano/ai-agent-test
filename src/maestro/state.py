"""Durable on-disk state for one game build.

Each game gets its own run directory under <working_directory>/runs/<run_id>/.
Inside it:
  - <component_id>.json   one file per artifact component (a generic Module's authored output)
  - spec.json             the frozen contract
  - story_state.json      continuity bible
  - game/                 the codegen path's TypeScript source (main.ts + system files,
                           manifest.json) — written directly by maestro.codegen.tools, not
                           through the component mechanism above

This is the source of truth. The agent's transcript is not used as memory; each
step rebuilds context from here.
"""

import json
import os
import uuid
from pathlib import Path
from typing import Dict, List, Optional

SPEC_FILE = "spec.json"
STORY_STATE_FILE = "story_state.json"
WAIVERS_FILE = "waivers.json"
OWNER_FILE = "owner.json"
CHARGED_FILE = "charged.json"

# Files that live in the run dir but are NOT artifact components.
_RESERVED = {SPEC_FILE, STORY_STATE_FILE, WAIVERS_FILE, OWNER_FILE, CHARGED_FILE}


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

    # ── ownership (the run's owning user; set at create) ──────────────────────
    def write_owner(self, user_id: str) -> None:
        self._write(OWNER_FILE, {"user_id": user_id})

    def read_owner(self) -> Optional[str]:
        data = self._read(OWNER_FILE)
        return data.get("user_id") if data else None

    # ── billing (durable "this run has been charged once" marker) ──────────────
    def is_charged(self) -> bool:
        """True once this run has been charged for a build. The charge is per-run and idempotent:
        a build enqueued for an already-charged run (re-trigger, resume-after-crash) is never
        deducted again — charged stays charged as long as the run can eventually finish."""
        return self._read(CHARGED_FILE) is not None

    def mark_charged(self) -> None:
        self._write(CHARGED_FILE, {"charged": True})

    # ── story state (Phase 6) ─────────────────────────────────────────────────
    def write_story_state(self, state: Dict) -> None:
        self._write(STORY_STATE_FILE, state)

    def read_story_state(self) -> Optional[Dict]:
        return self._read(STORY_STATE_FILE)

    def read_waivers(self) -> List[Dict]:
        return self._read(WAIVERS_FILE) or []

    # ── io ────────────────────────────────────────────────────────────────────
    def _write(self, filename: str, data) -> None:
        # Atomic: a concurrent load_artifact() (parallel fixes) must never read a half-written
        # file. Write a unique temp in the same dir, then os.replace (atomic on POSIX) — a reader
        # sees either the old complete file or the new one, never a truncated one.
        path = self.run_dir / filename
        tmp = self.run_dir / f".{filename}.{uuid.uuid4().hex}.tmp"
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def _read(self, filename: str):
        path = self.run_dir / filename
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
