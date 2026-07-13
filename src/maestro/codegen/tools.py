"""The bounded write/read tools a codegen Fix dispatches through Services.

A game is a folder of ES modules. A fix produces ONE whole file as a fenced ```js block (raw
completion, not a tool-call arg) and dispatches `write_game_file` with its name — bounded output, so
fixing one system can't delete another. `read_game_file` pulls a file back.
"""

import re

from maestro.codegen.gates import game_dir


def _safe(name: str) -> str:
    """A flat .ts filename inside the game folder — no paths, no traversal."""
    name = (name or "main.ts").strip().replace("\\", "/").split("/")[-1]
    if not name.endswith(".ts"):
        name = re.sub(r"\.js$", "", name) + ".ts"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def build_codegen_tools(state) -> dict:
    def write_game_file(code: str = "", file: str = "main.ts", **_) -> dict:
        if not code or not code.strip():
            return {"ok": False, "error": "empty code — output the complete file as one ```js block"}
        d = game_dir(state.run_dir)
        d.mkdir(parents=True, exist_ok=True)
        name = _safe(file)
        (d / name).write_text(code, encoding="utf-8")
        return {"ok": True, "file": name, "chars": len(code)}

    def read_game_file(file: str = "main.ts", **_) -> dict:
        path = game_dir(state.run_dir) / _safe(file)
        return {"ok": True, "content": path.read_text(encoding="utf-8") if path.exists() else ""}

    return {"write_game_file": write_game_file, "read_game_file": read_game_file}
