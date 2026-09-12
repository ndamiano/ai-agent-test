#!/usr/bin/env python3
"""Pre-commit comment audit: list the comment and docstring lines this commit
adds, sorted by whether their shape is on the allowlist, and ask. Non-interactive
callers are blocked — an agent does not approve its own prose."""
import ast
import os
import re
import subprocess
import sys

TAGLINE_MAX = 3
BLOCK_MAX = 2

COMMENT_RES = {
    (".py",): re.compile(r"(^\s*#)|(\s#\s)"),
    (".js", ".jsx", ".ts", ".tsx"): re.compile(
        r"(^\s*//)|(\s//\s)|(/\*)|(\*/)|(^\s*\*\s)"
    ),
    (".css",): re.compile(r"/\*"),
    (".html",): re.compile(r"<!--"),
}
PURE_COMMENT_RES = {
    (".py",): re.compile(r"^\s*#"),
    (".js", ".jsx", ".ts", ".tsx"): re.compile(r"^\s*(//|/\*|\*)"),
    (".css",): re.compile(r"^\s*/\*"),
    (".html",): re.compile(r"^\s*<!--"),
}

ALLOWLIST = """
ALLOWED — three shapes, nothing else:
  1. ONE line on a function, only when the name and signature do not carry it.
  2. A file tagline: what this file is, at the top, 1-3 lines.
  3. A magic number's provenance: its source, when the number cannot be
     reasoned about from the code alone.

DELETE everything else. Most often: a line restating the declaration under it,
a module docstring grown into paragraphs, a measurement or incident behind a
choice, and any contrast with what the code replaced. Those go in the commit
message, or nowhere.

A shape being allowed is not a pass — an allowed shape that says what the code
already says is still deleted.
"""

AGENT_VERDICT = """
If you are an agent reading this: "these ones are fine" is not available to you.
That judgement is what this hook exists to take away. For each line above, name
which of the three allowed shapes it is, or delete it. Default to deleting.
Re-stage and commit. Do not pass --no-verify, and do not ask for this hook to be
disabled.
"""


def pattern_for(path, table=COMMENT_RES):
    ext = os.path.splitext(path)[1]
    for exts, rx in table.items():
        if ext in exts:
            return rx
    return None


def staged_blob(path):
    out = subprocess.run(["git", "show", f":{path}"], capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


def docstring_spans(path, blob):
    if not path.endswith(".py"):
        return []
    try:
        tree = ast.parse(blob)
    except (SyntaxError, ValueError):
        return []
    spans = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            spans.append((first.lineno, first.end_lineno or first.lineno,
                          isinstance(node, ast.Module)))
    return spans


def block_size(lines, lineno, rx):
    """Contiguous whole-line comments around a 1-indexed line."""
    if rx is None or lineno > len(lines) or not rx.match(lines[lineno - 1]):
        return 1
    n, i = 1, lineno - 1
    while i > 0 and rx.match(lines[i - 1]):
        n, i = n + 1, i - 1
    i = lineno
    while i < len(lines) and rx.match(lines[i]):
        n, i = n + 1, i + 1
    return n


def classify(path, lineno, blob, spans):
    for start, end, is_module in spans:
        if start <= lineno <= end:
            size = end - start + 1
            if is_module:
                return ("tagline", size <= TAGLINE_MAX,
                        f"module docstring, {size} lines")
            return ("docstring", size == 1, f"docstring, {size} lines")
    lines = blob.splitlines()
    size = block_size(lines, lineno, pattern_for(path, PURE_COMMENT_RES))
    return ("comment", size <= BLOCK_MAX, f"comment block, {size} lines")


def added_prose():
    diff = subprocess.run(
        ["git", "diff", "--cached", "--unified=0", "--no-color"],
        capture_output=True, text=True, check=True,
    ).stdout
    found = []
    path, rx, blob, spans, lineno = None, None, "", [], 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            rx = pattern_for(path)
            blob = staged_blob(path)
            spans = docstring_spans(path, blob)
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            lineno = int(m.group(1)) if m else 0
        elif line.startswith("+") and not line.startswith("+++"):
            text = line[1:]
            in_doc = any(s <= lineno <= e for s, e, _ in spans)
            if (in_doc or (rx and rx.search(text))) and text.strip():
                kind, ok, why = classify(path, lineno, blob, spans)
                found.append((path, lineno, text.strip(), kind, ok, why))
            lineno += 1
    return found


def report(rows, header):
    if not rows:
        return
    print(f"\n{header}\n", file=sys.stderr)
    for path, lineno, text, kind, _, why in rows:
        print(f"  {path}:{lineno}  [{kind}: {why}]", file=sys.stderr)
        print(f"    {text}", file=sys.stderr)


def main():
    prose = added_prose()
    if not prose:
        return 0

    over = [r for r in prose if not r[4]]
    within = [r for r in prose if r[4]]
    report(over, f"{len(over)} OVER BUDGET — no allowed shape is this long:")
    report(within, f"{len(within)} within an allowed shape — does each say "
                   f"what the code cannot?")
    print(ALLOWLIST, file=sys.stderr)

    try:
        with open("/dev/tty") as tty:
            print("Keep them and commit? [y/N] ", end="", file=sys.stderr, flush=True)
            answer = tty.readline().strip().lower()
        return 0 if answer in ("y", "yes") else 1
    except OSError:
        print(AGENT_VERDICT, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
