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


WRAPPED = """// game.js — the whole simulation in one closure, exposed on window.__game.
(function () {
  'use strict';
  const TICK = 1 / 60;
  function reset(seed) {
    state = { seed };
  }
  function step(dt) {
    function tick() { }
    tick();
  }
  window.__game = { reset, step };
})();
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


def test_a_file_that_is_one_closure_is_mapped_one_level_in(tmp_path):
    out = code_map.render(_project(tmp_path, {"game.js": WRAPPED}))
    assert out.splitlines() == [
        "game.js (14 lines)",
        "  2-13  (function () {…})()",
        "  4  TICK = 1 / 60  (private)",
        "  5-7  reset(seed)  (private)",
        "  8-11  step(dt)  (private)",
        "      9  tick()",
    ]


DESIGN = """# 0. SCOPE
asked for a thing
## 0.1 Asked
## 0.2 Tiers
# 9. TESTS
tests run in the page

| Tier | Test file |
# 10. BUILD ORDER
last"""


def test_a_markdown_file_is_mapped_by_its_headings(tmp_path):
    out = code_map.render(_project(tmp_path, {"design/design.md": DESIGN}))
    assert out.splitlines() == [
        "design/design.md (10 lines)",
        "  1-4  0. SCOPE",
        "    3  0.1 Asked",
        "    4  0.2 Tiers",
        "  5-8  9. TESTS",
        "  9-10  10. BUILD ORDER",
    ]
