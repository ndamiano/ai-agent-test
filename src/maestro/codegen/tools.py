"""The bounded write/read tools a codegen Fix dispatches through Services.

The fix produces a whole `game.js` as a fenced ```js block (raw completion, not a tool-call arg —
JSON-escaping a 300-line file is the fragility the fenced block avoids), then dispatches
`write_game_file` to persist it. `read_game_file` lets a check/prompt pull the current code back.
"""

from maestro.codegen.gates import game_path


def build_codegen_tools(state) -> dict:
    def write_game_file(code: str = "", **_) -> dict:
        if not code or not code.strip():
            return {"ok": False, "error": "empty code — output the complete module as one ```js block"}
        game_path(state.run_dir).write_text(code, encoding="utf-8")
        return {"ok": True, "chars": len(code)}

    def read_game_file(**_) -> dict:
        path = game_path(state.run_dir)
        return {"ok": True, "content": path.read_text(encoding="utf-8") if path.exists() else ""}

    return {"write_game_file": write_game_file, "read_game_file": read_game_file}
