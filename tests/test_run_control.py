"""RunControl — the cross-thread pause/resume signal + the per-run registry.

The build is completion-driven now (build_chain.advance checks control.paused at each step and stops,
persisting the cursor; resume re-drives), so the signal itself is what matters here. The build-level
pause behaviour is covered in test_build_chain.
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.run_control import RunControl, get, get_or_create, remove


def test_pause_sets_flag_and_resume_clears():
    c = RunControl()
    assert not c.paused
    c.request_pause()
    assert c.paused
    c.set_status("paused")
    assert c.status == "paused"
    c.request_resume()
    assert not c.paused


def test_wait_while_paused_unblocks_on_resume():
    c = RunControl()
    c.request_pause()
    done = []

    def waiter():
        c.wait_while_paused()
        done.append(1)

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.05)
    assert not done            # still blocked while paused
    c.request_resume()
    t.join(timeout=2)
    assert done == [1]


def test_registry_lifecycle():
    a = get_or_create("run-x")
    assert get("run-x") is a
    assert get_or_create("run-x") is a
    remove("run-x")
    assert get("run-x") is None
