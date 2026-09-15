"""The project as one message: every file, its imports, and each declaration or markdown heading
with its line range. Regex, not a parser."""

import re
from pathlib import Path
from typing import List, Optional, Tuple

_SKIP_DIRS = ("assets/", "world/")
_TEXT = (".js", ".mjs", ".html", ".css", ".json", ".md")

_IMPORT = re.compile(r'^import\s+[^;\n]*?from\s+[\'"]([^\'"]+)[\'"]', re.M)
_TOP = re.compile(r'^(export\s+)?(?:async\s+)?(?:function\s*\*?\s*(\w+)\s*\(|(?:const|let|var)\s+(\w+)\s*=|class\s+(\w+))', re.M)
_INNER = re.compile(r'^[ \t]+(?:async\s+)?(?:function\s*\*?\s*(\w+)\s*\(|(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)\n]*\)|\w+)\s*=>|(\w+)\s*\([^)\n]*\)\s*\{)', re.M)
_EXPORT_LIST = re.compile(r'^export\s*\{([^}]*)\}', re.M)
_HEADING = re.compile(r'^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$', re.M)
_WRAPPER = re.compile(r'^\(\s*(?:async\s+)?(?:function\s*\*?\s*\w*\s*\([^)\n]*\)|\([^)\n]*\))\s*(?:=>\s*)?\{', re.M)


def render(root: Path) -> str:
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
        elif p.suffix == ".md":
            out.extend(_headings(src))
    return "\n".join(out)


def _headings(src: str) -> List[str]:
    heads = [(m.start(), len(m.group(1)), m.group(2)) for m in _HEADING.finditer(src)]
    total = src.count("\n") + 1
    lines = []
    for i, (at, level, text) in enumerate(heads):
        start = src.count("\n", 0, at) + 1
        end = total
        for at2, level2, _ in heads[i + 1:]:
            if level2 <= level:
                end = src.count("\n", 0, at2)
                break
        lines.append(f"  {'  ' * (level - 1)}{_range(start, end)}  {text}")
    return lines


def _is_vendor(p: Path) -> bool:
    from maestro.codegen.tools import _VENDOR_FILES
    return p.name in _VENDOR_FILES


def _short(spec: str) -> str:
    return spec.replace("../", "").replace("./", "")


def _declarations(src: str) -> List[str]:
    lines = _scan(src, _TOP, 0)
    for m in _WRAPPER.finditer(src):
        close = _matching(src, m.end() - 1)
        if close is None:
            continue
        start, end = src.count("\n", 0, m.start()) + 1, src.count("\n", 0, close) + 1
        lines.append(f"  {_range(start, end)}  {m.group(0).strip()}…}})()")
        body = src[m.end():close]
        indent = re.search(r'^([ \t]+)\S', body, re.M)
        if indent:
            top = re.compile("^" + re.escape(indent.group(1)) + _TOP.pattern[1:], re.M)
            lines.extend(_scan(body, top, src.count("\n", 0, m.end())))
    for m in _EXPORT_LIST.finditer(src):
        lines.append("  exports " + " ".join(m.group(1).split()))
    return lines


def _scan(src: str, top: "re.Pattern", base: int) -> List[str]:
    lines = []
    for m in top.finditer(src):
        name = m.group(2) or m.group(3) or m.group(4)
        start, end = _span(src, m)
        sig = _signature(src, m)
        mark = "" if m.group(1) else "  (private)"
        lines.append(f"  {_range(base + start, base + end)}  {name}{sig}{mark}")
        if m.group(2) or m.group(4):
            body = src[m.end():_offset_of_line(src, end + 1)]
            inner = base + start - 1
            for im in _INNER.finditer(body):
                iname = im.group(1) or im.group(2) or im.group(3)
                if iname in ("if", "for", "while", "switch", "catch", "return"):
                    continue
                istart, iend = _span(body, im)
                lines.append(f"      {_range(inner + istart, inner + iend)}  {iname}{_signature(body, im)}")
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
    """1-based first and last line: to the closing bracket when the first line opens one."""
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
    """The bracket opening the body on the first line, past any parameter list."""
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
    """Index of the bracket closing the one at `i`, skipping strings and comments."""
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
