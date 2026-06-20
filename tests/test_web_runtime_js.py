"""Drive the JS runtime's pure-core unit tests through node, so they run in the pytest suite.
Skipped when node is unavailable (the Python compile tests still cover the build path)."""
import shutil
import subprocess
from pathlib import Path

import pytest

_TEST = Path(__file__).parent / "web_runtime_core.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_web_runtime_core():
    proc = subprocess.run(["node", str(_TEST)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
