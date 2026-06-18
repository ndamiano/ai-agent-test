import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.tools import _normalize_script

# same regex validate.check_each_node_min_lines uses to count dialogue/narration lines
_QUOTED_RE = re.compile(r'^[ \t]*(?:\w+\s+)?"', re.MULTILINE)


def test_clean_script_is_untouched():
    clean = 'label start:\n    scene bg\n    mira "Hello."\n'
    assert _normalize_script(clean) == clean


def test_single_level_overescape():
    over = 'label start:\\n    mira \\"Hi.\\"\n'
    assert _normalize_script(over) == 'label start:\n    mira "Hi."\n'


def test_backslash_welded_to_real_newline():
    # the failing-run flavour: a stray backslash clinging to every real newline
    over = "label start:\\\n    scene bg\\\n    mira \\\\\"Hi.\\\\\\\\\"\n"
    fixed = _normalize_script(over)
    assert "\\\n" not in fixed                       # no line-continuation artifacts
    assert 'mira "Hi."' in fixed                     # quotes fully unescaped
    assert len(_QUOTED_RE.findall(fixed)) == 1       # the dialogue line is now countable


def test_stacked_backslashes_before_quote_collapse():
    assert _normalize_script('a \\\\\\"x\\\\\\"') == 'a "x"'


def test_idempotent():
    over = "label start:\\\n    mira \\\\\"Hi.\\\\\\\\\"\n"
    once = _normalize_script(over)
    assert _normalize_script(once) == once


def test_non_string_passthrough():
    assert _normalize_script(None) is None
    assert _normalize_script({"a": 1}) == {"a": 1}
