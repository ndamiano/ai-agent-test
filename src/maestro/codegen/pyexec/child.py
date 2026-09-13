"""The confined side: import what is allowed, lock the process down, then run the model's program.

Order is the whole design. Every import happens FIRST, because the filter denies `openat` and an
import after it would fail; the filter goes on SECOND, before a single byte of model code is
compiled; the program runs THIRD, with the tool names bound to stubs that carry each call over the
socket to the parent. A failure to install the filter exits non-zero rather than running the
program unconfined.
"""

from __future__ import annotations

# Everything the program may use, imported before the door closes. Anything absent here cannot be
# imported later, whatever the program asks for.
import json  # noqa: F401
import math  # noqa: F401
import random  # noqa: F401
import re  # noqa: F401
import itertools  # noqa: F401
import collections  # noqa: F401
import statistics  # noqa: F401
import string  # noqa: F401
import textwrap  # noqa: F401
import functools  # noqa: F401
import operator  # noqa: F401
import copy  # noqa: F401
import heapq  # noqa: F401
import bisect  # noqa: F401
import difflib  # noqa: F401
import hashlib  # noqa: F401
import unicodedata  # noqa: F401
import datetime  # noqa: F401
import decimal  # noqa: F401
import fractions  # noqa: F401
import base64  # noqa: F401
import io
import os
import socket
import sys
import traceback

from maestro.codegen.pyexec import rpc, seccomp

# The tools reach the parent as keyword arguments, but a program calls them the way any Python
# function is called — `read_file("game.js")` is what the model writes, and refusing it fails
# nearly every program. These are the signatures build.txt documents, in order.
TOOLS = {
    "list_files": (),
    "read_file": ("path", "offset", "lines"),
    "write_file": ("path", "content"),
    "edit_file": ("path", "old_text", "new_text"),
    "generate_media": ("id", "kind", "subject", "style", "details"),
    "compose_world": ("description", "seed"),
    "check_syntax": ("paths",),
    "play": ("js", "seconds"),
    "done": ("summary",),
}


def _stub(sock, name):
    names = TOOLS[name]

    def call(*args, **kwargs):
        if len(args) > len(names):
            raise TypeError(f"{name}() takes at most {len(names)} arguments "
                            f"({', '.join(names) or 'none'}) but {len(args)} were given")
        for key, value in zip(names, args):
            if key in kwargs:
                raise TypeError(f"{name}() got two values for {key!r}")
            kwargs[key] = value
        rpc.send(sock, {"tool": name, "kwargs": kwargs})
        reply = rpc.recv(sock)
        if reply is None:
            raise SystemExit("the session ended while a tool call was in flight")
        return reply.get("value")
    call.__name__ = name
    return call


def main() -> int:
    sock = socket.socket(fileno=int(os.environ["PYEXEC_FD"]))
    code = sys.stdin.read()

    try:
        seccomp.install()
    except Exception as e:                       # never run the program unconfined
        print(f"pyexec: could not confine the process: {e}", file=sys.stderr)
        return 3

    # The modules the harness needed to start are not the program's to inherit. This is tidiness,
    # not the boundary: os and a few others are FROZEN into the interpreter and import from
    # bytecode with no file read, so a program can still reach the name. What makes that harmless
    # is the filter — every syscall those modules exist to make is denied — and an environment
    # holding nothing but the fd number and a PATH.
    # sys, importlib and traceback stay: the import machinery and this function's own error
    # handling are built on them, and their syscalls are denied like everything else.
    for name in ("os", "socket", "subprocess", "shutil", "pathlib", "ctypes", "tempfile",
                 "glob", "platform", "inspect", "pickle", "webbrowser", "urllib", "http"):
        sys.modules.pop(name, None)

    out = io.StringIO()
    ns = {"__name__": "__main__", "__builtins__": __builtins__}
    ns.update({name: _stub(sock, name) for name in TOOLS})

    status = 0
    sys.stdout = out
    try:
        exec(compile(code, "<program>", "exec"), ns)
    except SystemExit:
        pass
    except BaseException:
        # The harness's own frames are not the model's business: the traceback starts at the
        # program.
        tb = traceback.format_exc()
        keep = [line for line in tb.splitlines(keepends=True)
                if "pyexec/child.py" not in line and "in main" not in line]
        out.write("\n" + "".join(keep))
        status = 1
    finally:
        sys.stdout = sys.__stdout__

    rpc.send(sock, {"done": True, "stdout": out.getvalue(), "status": status})
    sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
