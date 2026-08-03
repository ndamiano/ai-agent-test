#!/usr/bin/env python3
"""Pre-commit comment audit: show every comment line this commit adds and ask
whether it belongs (CLAUDE.md: comments only for non-obvious WHY).

Interactive commit: prompts y/N on the terminal.
Non-interactive (agents, tooling): blocks and prints the list; re-run the same
commit with COMMENTS_REVIEWED=1 after reviewing, or use --no-verify.
"""
import os
import re
import subprocess
import sys

COMMENT_RES = {
    (".py",): re.compile(r"(^\s*#)|(\s#\s)"),
    (".js", ".jsx", ".ts", ".tsx"): re.compile(
        r"(^\s*//)|(\s//\s)|(/\*)|(\*/)|(^\s*\*\s)"
    ),
    (".css",): re.compile(r"/\*"),
    (".html",): re.compile(r"<!--"),
}


def pattern_for(path):
    ext = os.path.splitext(path)[1]
    for exts, rx in COMMENT_RES.items():
        if ext in exts:
            return rx
    return None


def added_comments():
    diff = subprocess.run(
        ["git", "diff", "--cached", "--unified=0", "--no-color"],
        capture_output=True, text=True, check=True,
    ).stdout
    found = []
    path, rx, lineno = None, None, 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            rx = pattern_for(path)
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            lineno = int(m.group(1)) if m else 0
        elif line.startswith("+") and not line.startswith("+++"):
            text = line[1:]
            if rx and rx.search(text):
                found.append((path, lineno, text.strip()))
            lineno += 1
    return found


def main():
    comments = added_comments()
    if not comments:
        return 0

    print("\nComments added in this commit:\n", file=sys.stderr)
    for path, lineno, text in comments:
        print(f"  {path}:{lineno}", file=sys.stderr)
        print(f"    {text}", file=sys.stderr)
    print(
        "\nDo they belong? Standard: no WHAT, no narration — "
        "only non-obvious WHY (hidden constraint, workaround, invariant).",
        file=sys.stderr,
    )

    if os.environ.get("COMMENTS_REVIEWED") == "1":
        print("COMMENTS_REVIEWED=1 — proceeding.", file=sys.stderr)
        return 0

    try:
        with open("/dev/tty") as tty:
            print("Keep them and commit? [y/N] ", end="", file=sys.stderr, flush=True)
            answer = tty.readline().strip().lower()
        return 0 if answer in ("y", "yes") else 1
    except OSError:
        print(
            "\nNo terminal to confirm on. Review the list above, then either "
            "fix the comments or re-commit with COMMENTS_REVIEWED=1.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
