"""Shared bootstrap for the debug harnesses: put src/ on the path and register checks.
Import this first. Keeps each harness runnable as `python debug/<harness>.py ...` with only
the venv active (no PYTHONPATH needed)."""
import sys, logging
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

logging.basicConfig(level=logging.WARNING)

from renpy.ir_checks import register_all  # noqa: E402
register_all()

DEBUG = Path(__file__).resolve().parent
WORK = DEBUG / "work"
FIXTURES = DEBUG / "fixtures"
WORK.mkdir(exist_ok=True)
