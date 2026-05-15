import sys
from pathlib import Path

EVAL_DIR = Path(__file__).parent
REPO_ROOT = EVAL_DIR.parent
SRC_DIR = REPO_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

BRIEFS_DIR = EVAL_DIR / "briefs"
RUBRICS_DIR = EVAL_DIR / "rubrics"
FIXTURES_DIR = EVAL_DIR / "fixtures"
RESULTS_DIR = EVAL_DIR / "results"
