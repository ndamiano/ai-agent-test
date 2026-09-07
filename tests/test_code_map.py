from pathlib import Path

from maestro.codegen import code_map

COMBAT = """import { weaponOf } from './units.js';
import { TERRAIN_TYPES } from '../data/terrain.js';

const TRIANGLE = { sword: 'axe', axe: 'lance', lance: 'sword' };

export function accuracy(w) { return w ? w.hit : 0; }

export function triangleBonus(aType, dType) {
  if (TRIANGLE[aType] === dType) return 15;
  if (TRIANGLE[dType] === aType) return -15;
  return 0;
}

export function resolveCombat(attacker, defender, battle, { preview = false, log = () => {} } = {}) {
  const events = [];
  function strike(a, d) {   // "{" in a comment, and a ")" in this string: ")"
    const dmg = Math.max(0, a.atk - d.def);
    events.push({ dmg });
  }
  const finish = (won) => { log(`done ${won ? '{' : ''}`); };
  strike(attacker, defender);
  finish(true);
  return events;
}

export const XP = [10, 20, 30];
export const CONFIG = {
  base: 1,
};
"""

SCREEN = """export function BattleScreen({ battle }) {
  let t = 0;
  function startTurn() {
    t = 0;
  }
  return {
    init() { startTurn(); },
    update(dt) { t += dt; },
    draw(ctx) {
      ctx.fillRect(0, 0, 1, 1);
    },
  };
}
"""


def _project(tmp_path, files):
    for name, src in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return tmp_path


def test_every_declaration_gets_its_line_range(tmp_path):
    out = code_map.render(_project(tmp_path, {"systems/combat.js": COMBAT}))
    lines = out.splitlines()
    assert lines[0] == "systems/combat.js (30 lines)  imports units.js, data/terrain.js"
    assert "  4  TRIANGLE = {…}  (private)" in lines
    assert "  6  accuracy(w)" in lines
    assert "  8-12  triangleBonus(aType, dType)" in lines
    # The range runs to the function's own closing brace, past braces in comments and strings
    # and past a destructured default parameter on the signature line.
    assert "  14-24  resolveCombat(attacker, defender, battle, { preview = false, log = () => {} } = {})" in lines
    assert "  26  XP = […]" in lines
    assert "  27-29  CONFIG = {…}" in lines


def test_functions_one_level_in_are_listed_under_their_owner(tmp_path):
    out = code_map.render(_project(tmp_path, {"systems/combat.js": COMBAT, "screens/battle.js": SCREEN}))
    lines = out.splitlines()
    assert "      16-19  strike(a, d)" in lines
    assert "      20  finish(won)" in lines
    i = lines.index("screens/battle.js (14 lines)")
    assert lines[i + 1:i + 6] == [
        "  1-13  BattleScreen({ battle })",
        "      3-5  startTurn()",
        "      7  init()",
        "      8  update(dt)",
        "      9-11  draw(ctx)",
    ]


def test_art_and_the_vendored_renderer_are_not_in_the_map(tmp_path, monkeypatch):
    from maestro.codegen import tools
    monkeypatch.setattr(tools, "_VENDOR_FILES", {"three.module.js"})
    root = _project(tmp_path, {"index.html": "<canvas>", "three.module.js": "export const X = 1;",
                               "assets/hero.png": "png", "assets/hero.json": "{}",
                               "lib/input.js": "export function keys() {}"})
    out = code_map.render(root)
    assert out.splitlines() == ["index.html (1 lines)", "lib/input.js (1 lines)", "  1  keys()"]


def test_an_empty_project_maps_to_nothing(tmp_path):
    assert code_map.render(tmp_path) == ""
