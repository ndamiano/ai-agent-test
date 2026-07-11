import pytest

from worldgen.wfc import ContradictionError, collapse, from_palette

CHAIN_TILES = ["sea", "reef", "shallows", "beach", "jungle"]
CHAIN_ADJ = [
    ["sea", "reef"],
    ["reef", "shallows"],
    ["shallows", "beach"],
    ["beach", "jungle"],
]


def _adjacency_set(tiles, adjacency):
    allowed = {t: {t} for t in tiles}
    for a, b in adjacency:
        allowed[a].add(b)
        allowed[b].add(a)
    return allowed


def _assert_grid_consistent(grid, tiles, adjacency):
    allowed = _adjacency_set(tiles, adjacency)
    h = len(grid)
    w = len(grid[0])
    for y in range(h):
        assert len(grid[y]) == w
        for x in range(w):
            tile = grid[y][x]
            assert tile in tiles
            if x + 1 < w:
                assert grid[y][x + 1] in allowed[tile]
            if y + 1 < h:
                assert grid[y + 1][x] in allowed[tile]


def test_determinism_same_seed():
    g1 = collapse(20, 15, CHAIN_TILES, CHAIN_ADJ, seed=42)
    g2 = collapse(20, 15, CHAIN_TILES, CHAIN_ADJ, seed=42)
    assert g1 == g2


def test_determinism_different_seed():
    g1 = collapse(20, 15, CHAIN_TILES, CHAIN_ADJ, seed=1)
    g2 = collapse(20, 15, CHAIN_TILES, CHAIN_ADJ, seed=2)
    assert g1 != g2


def test_adjacency_satisfied_full_grid():
    grid = collapse(40, 30, CHAIN_TILES, CHAIN_ADJ, seed=7)
    _assert_grid_consistent(grid, CHAIN_TILES, CHAIN_ADJ)


def test_pins_present_verbatim():
    pins = {(0, 0): "sea", (5, 5): "jungle", (10, 2): "beach"}
    grid = collapse(20, 15, CHAIN_TILES, CHAIN_ADJ, pins=pins, seed=3)
    for (x, y), tile in pins.items():
        assert grid[y][x] == tile
    _assert_grid_consistent(grid, CHAIN_TILES, CHAIN_ADJ)


def test_weights_bias_dominant_tile():
    tiles = ["A", "B"]
    adjacency = [["A", "B"]]
    grid = collapse(30, 30, tiles, adjacency, weights={"A": 10.0, "B": 1.0}, seed=5)
    flat = [t for row in grid for t in row]
    count_a = flat.count("A")
    count_b = flat.count("B")
    assert count_a > count_b
    assert count_a / len(flat) > 0.7


def test_tight_pinning_recovers_via_restarts():
    tiles = ["A", "B", "C"]
    adjacency = [["A", "B"], ["B", "C"]]
    pins = {}
    for x in range(0, 12, 2):
        pins[(x, 0)] = "A" if (x // 2) % 2 == 0 else "C"
    grid = collapse(12, 6, tiles, adjacency, pins=pins, seed=11, max_restarts=20)
    for (x, y), tile in pins.items():
        assert grid[y][x] == tile
    _assert_grid_consistent(grid, tiles, adjacency)


def test_restart_mechanism_retries_then_succeeds(monkeypatch):
    import worldgen.wfc as wfc

    calls = {"n": 0}

    def fake_solve_once(w, h, n, allowed_masks, weight_list, pin_cells, domain_cells,
                        entropy_cache, rng):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ContradictionError()
        return [0] * (w * h)

    monkeypatch.setattr(wfc, "_solve_once", fake_solve_once)
    grid = wfc.collapse(3, 3, ["A", "B"], [["A", "B"]], seed=0, max_restarts=5)
    assert calls["n"] == 3
    assert all(cell == "A" for row in grid for cell in row)


def test_restart_mechanism_raises_after_exhausting(monkeypatch):
    import worldgen.wfc as wfc

    def fake_solve_once(*args, **kwargs):
        raise ContradictionError()

    monkeypatch.setattr(wfc, "_solve_once", fake_solve_once)
    with pytest.raises(ContradictionError):
        wfc.collapse(3, 3, ["A", "B"], [["A", "B"]], seed=0, max_restarts=4)


def test_impossible_pins_raise_contradiction():
    tiles = ["A", "B", "C"]
    adjacency = [["A", "B"]]
    pins = {(0, 0): "A", (1, 0): "C"}
    with pytest.raises(ContradictionError):
        collapse(5, 5, tiles, adjacency, pins=pins, seed=0, max_restarts=3)


def test_unknown_tile_in_adjacency_raises_value_error():
    with pytest.raises(ValueError):
        collapse(5, 5, ["A", "B"], [["A", "Z"]], seed=0)


def test_unknown_tile_in_pins_raises_value_error():
    with pytest.raises(ValueError):
        collapse(5, 5, ["A", "B"], [["A", "B"]], pins={(0, 0): "Z"}, seed=0)


def test_unknown_tile_in_weights_raises_value_error():
    with pytest.raises(ValueError):
        collapse(5, 5, ["A", "B"], [["A", "B"]], weights={"Z": 2.0}, seed=0)


def test_pin_out_of_bounds_raises_value_error():
    with pytest.raises(ValueError):
        collapse(5, 5, ["A", "B"], [["A", "B"]], pins={(10, 10): "A"}, seed=0)


def test_from_palette():
    palette = {
        "tiles": CHAIN_TILES,
        "adjacency": CHAIN_ADJ,
        "weights": {"sea": 2.0},
    }
    tiles, adjacency, weights = from_palette(palette)
    assert tiles == CHAIN_TILES
    assert adjacency == CHAIN_ADJ
    assert weights == {"sea": 2.0}


def test_island_transition_demo(capsys):
    tiles = ["sea", "reef", "shallows", "beach", "jungle"]
    adjacency = [
        ["sea", "reef"],
        ["reef", "shallows"],
        ["shallows", "beach"],
        ["beach", "jungle"],
    ]
    w, h = 40, 30
    pins = {}
    for x in range(w):
        pins[(x, 0)] = "sea"
        pins[(x, h - 1)] = "sea"
    for y in range(h):
        pins[(0, y)] = "sea"
        pins[(w - 1, y)] = "sea"
    cx, cy = w // 2, h // 2
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            pins[(cx + dx, cy + dy)] = "jungle"

    grid = collapse(w, h, tiles, adjacency, pins=pins, seed=99)

    for (x, y), tile in pins.items():
        assert grid[y][x] == tile
    _assert_grid_consistent(grid, tiles, adjacency)

    glyph = {"sea": "~", "reef": "r", "shallows": ".", "beach": "b", "jungle": "#"}
    print()
    for row in grid:
        print("".join(glyph[t] for t in row))


def test_domains_restrict_cells():
    import worldgen.wfc as wfc

    grid = wfc.collapse(6, 4, ["A", "B", "C"], [["A", "B"], ["B", "C"]],
                        domains={(x, y): {"A", "B"} for y in range(4) for x in range(3)},
                        pins={(5, 0): "C"}, seed=7)
    assert all(grid[y][x] in ("A", "B") for y in range(4) for x in range(3))
    assert grid[0][5] == "C"


def test_domains_validation():
    import worldgen.wfc as wfc
    import pytest

    with pytest.raises(ValueError):
        wfc.collapse(3, 3, ["A"], [], domains={(0, 0): {"Z"}})
    with pytest.raises(ValueError):
        wfc.collapse(3, 3, ["A"], [], domains={(9, 9): {"A"}})
    with pytest.raises(ValueError):
        wfc.collapse(3, 3, ["A"], [], domains={(0, 0): set()})
