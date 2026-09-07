"""The project as one message: every file, what it imports, and each declaration with the lines it
occupies — so a model that has lost its transcript regains the whole picture without reading a
file, and reads by line range when it does.

After a compaction the model's own question is "does render.js export syncRunes, what does
resolveCombat take, which file owns the roster" — and its only answer was to read every file
again (measured 2026-09-06: 149 of 176 post-compaction reads were of files it had already read).
This map answers those questions for the whole project in a couple of thousand tokens.

Regex over JavaScript, not a parser: top-level exports and declarations, and the function
declarations one level inside them, because a screen written as one exported closure is where
the whole file's structure lives. A declaration's range runs to its matching close brace.
"""

import re
from pathlib import Path
from typing import List, Optional, Tuple

_SKIP_DIRS = ("assets/", "world/")
_TEXT = (".js", ".mjs", ".html", ".css", ".json")

_IMPORT = re.compile(r'^import\s+[^;\n]*?from\s+[\'"]([^\'"]+)[\'"]', re.M)
# `export function name(`, `export const name =`, `export class Name`, and the same without
# `export` at column 0 — a file's private top-level functions are part of its shape too.
_TOP = re.compile(r'^(export\s+)?(?:async\s+)?(?:function\s*\*?\s*(\w+)\s*\(|(?:const|let|var)\s+(\w+)\s*=|class\s+(\w+))', re.M)
# Function declarations nested one level in: `  function name(`, `  const name = (` … `) =>`,
# and `  name(args) {` methods on an object or class body.
_INNER = re.compile(r'^[ \t]+(?:async\s+)?(?:function\s*\*?\s*(\w+)\s*\(|(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)\n]*\)|\w+)\s*=>|(\w+)\s*\([^)\n]*\)\s*\{)', re.M)
_EXPORT_LIST = re.compile(r'^export\s*\{([^}]*)\}', re.M)


def render(root: Path) -> str:
    """The map of every source file under `root`, in path order, or "" for an empty project."""
    out = []
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if not p.is_file() or p.name.startswith("_") or rel.startswith(_SKIP_DIRS) \
                or p.suffix not in _TEXT or p.parent == root and p.suffix == ".js" and _is_vendor(p):
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        n = src.count("\n") + 1
        head = f"{rel} ({n} lines)"
        imports = [_short(i) for i in _IMPORT.findall(src)]
        if imports:
            head += "  imports " + ", ".join(imports)
        out.append(head)
        if p.suffix in (".js", ".mjs"):
            out.extend(_declarations(src))
    return "\n".join(out)


def _is_vendor(p: Path) -> bool:
    from maestro.codegen.tools import _VENDOR_FILES
    return p.name in _VENDOR_FILES


def _short(spec: str) -> str:
    return spec.replace("../", "").replace("./", "")


def _declarations(src: str) -> List[str]:
    lines = []
    for m in _TOP.finditer(src):
        name = m.group(2) or m.group(3) or m.group(4)
        start, end = _span(src, m)
        sig = _signature(src, m)
        mark = "" if m.group(1) else "  (private)"
        lines.append(f"  {_range(start, end)}  {name}{sig}{mark}")
        if m.group(2) or m.group(4):
            body = src[m.end():_offset_of_line(src, end + 1)]
            base = start - 1            # body starts mid-way through the declaration's first line
            for im in _INNER.finditer(body):
                iname = im.group(1) or im.group(2) or im.group(3)
                if iname in ("if", "for", "while", "switch", "catch", "return"):
                    continue
                istart, iend = _span(body, im)
                lines.append(f"      {_range(base + istart, base + iend)}  {iname}{_signature(body, im)}")
    for m in _EXPORT_LIST.finditer(src):
        lines.append("  exports " + " ".join(m.group(1).split()))
    return lines


def _range(start: int, end: int) -> str:
    return f"{start}" if start == end else f"{start}-{end}"


def _offset_of_line(src: str, line: int) -> int:
    pos = 0
    for _ in range(line - 1):
        nxt = src.find("\n", pos)
        if nxt < 0:
            return len(src)
        pos = nxt + 1
    return pos


def _signature(src: str, m: "re.Match") -> str:
    """`(a, b = 1)` for a function, ` = {…}` / ` = […]` / ` = <first 40 chars>` for a value."""
    i = m.end()
    if src[i - 1] == "(":
        close = _matching(src, i - 1)
        return "(" + " ".join(src[i:close].split()) + ")" if close else "(…)"
    if src[i - 1] == "{":
        args = re.search(r'(\([^)]*\))\s*\{$', m.group(0))
        return " ".join(args.group(1).split()) if args else ""
    if m.group(0).rstrip().endswith("=>"):
        args = re.search(r'=\s*(?:async\s*)?(\([^)]*\)|\w+)\s*=>$', m.group(0).rstrip())
        a = args.group(1) if args else "…"
        return " ".join(a.split()) if a.startswith("(") else f"({a})"
    rest = src[i:src.find("\n", i) if src.find("\n", i) >= 0 else len(src)].strip()
    if not rest or m.group(0).rstrip().endswith(tuple("(")):
        return ""
    if re.match(r'(?:async\s*)?\([^)]*\)\s*=>', rest) or rest.startswith("function"):
        args = re.match(r'(?:async\s*)?(\([^)]*\))', rest)
        return args.group(1) if args else "(…)"
    if rest.startswith("{"):
        return " = {…}"
    if rest.startswith("["):
        return " = […]"
    return " = " + rest[:40].rstrip(";")


def _span(src: str, m: "re.Match") -> Tuple[int, int]:
    """1-based first and last line of the declaration: to its closing brace or bracket when it
    opens one on its first line, else the line itself."""
    start = src.count("\n", 0, m.start()) + 1
    eol = src.find("\n", m.start())
    eol = len(src) if eol < 0 else eol
    tail = src[m.end() - 1]
    opener = (m.end() - 1 if tail == "{"
              else _body_opener(src, m.end() - 1 if tail == "(" else m.end(), eol))
    if opener is None:
        return start, start
    close = _matching(src, opener)
    if close is None:
        return start, start
    return start, src.count("\n", 0, close) + 1


def _body_opener(src: str, i: int, eol: int) -> Optional[int]:
    """The `{` or `[` that opens the declaration's body on its first line — past a parameter list
    if there is one, so a destructured default parameter's braces are never mistaken for it."""
    if i < len(src) and src[i] == "(":
        close = _matching(src, i)
        if close is None:
            return None
        i = close + 1
    j = i
    while j <= eol and j < len(src):
        c = src[j]
        if c in "{[":
            return j
        if c in "([":
            close = _matching(src, j)
            if close is None:
                return None
            j = close + 1
            continue
        j += 1
    return None


def _matching(src: str, i: int) -> Optional[int]:
    """Index of the bracket closing the one at `i`, skipping strings, template literals and
    comments; None when the file never closes it."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack = [pairs[src[i]]]
    j = i + 1
    n = len(src)
    while j < n and stack:
        c = src[j]
        if c in "\"'`":
            j = _skip_string(src, j)
            continue
        if src.startswith("//", j):
            j = src.find("\n", j)
            if j < 0:
                return None
            continue
        if src.startswith("/*", j):
            j = src.find("*/", j)
            if j < 0:
                return None
            j += 2
            continue
        if c in pairs:
            stack.append(pairs[c])
        elif c in ")]}":
            if c != stack[-1]:
                return None
            stack.pop()
            if not stack:
                return j
        j += 1
    return None


def _skip_string(src: str, i: int) -> int:
    q = src[i]
    j = i + 1
    n = len(src)
    while j < n:
        c = src[j]
        if c == "\\":
            j += 2
            continue
        if c == q:
            return j + 1
        if c == "\n" and q != "`":
            return j
        j += 1
    return n
