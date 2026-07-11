import heapq
import math
import random
from collections import deque

_NEIGHBORS = ((1, 0), (-1, 0), (0, 1), (0, -1))
_TIE_EPSILON = 1e-6


class ContradictionError(Exception):
    pass


def from_palette(palette):
    tiles = list(palette["tiles"])
    adjacency = [list(pair) for pair in palette["adjacency"]]
    weights = dict(palette["weights"]) if "weights" in palette else None
    return tiles, adjacency, weights


def _build_allowed_masks(tiles, index, adjacency):
    n = len(tiles)
    masks = [1 << i for i in range(n)]
    for a, b in adjacency:
        if a not in index:
            raise ValueError(f"unknown tile in adjacency: {a!r}")
        if b not in index:
            raise ValueError(f"unknown tile in adjacency: {b!r}")
        ia, ib = index[a], index[b]
        masks[ia] |= 1 << ib
        masks[ib] |= 1 << ia
    return masks


def _bits(mask):
    out = []
    while mask:
        lsb = mask & (-mask)
        out.append(lsb.bit_length() - 1)
        mask &= mask - 1
    return out


def _entropy(mask, weight_list, cache):
    cached = cache.get(mask)
    if cached is not None:
        return cached
    tile_ids = _bits(mask)
    total = sum(weight_list[t] for t in tile_ids)
    value = -sum(
        (weight_list[t] / total) * math.log(weight_list[t] / total)
        for t in tile_ids
        if weight_list[t] > 0
    )
    cache[mask] = value
    return value


def _choose_tile(mask, weight_list, rng):
    tile_ids = _bits(mask)
    total = sum(weight_list[t] for t in tile_ids)
    r = rng.random() * total
    upto = 0.0
    for t in tile_ids:
        upto += weight_list[t]
        if upto >= r:
            return t
    return tile_ids[-1]


def _solve_once(w, h, n, allowed_masks, weight_list, pin_cells, domain_cells, entropy_cache, rng):
    full_mask = (1 << n) - 1
    cells = w * h
    domains = [full_mask] * cells
    version = [0] * cells
    prop_queue = deque()
    in_queue = [False] * cells
    noise = [rng.random() * _TIE_EPSILON for _ in range(cells)]
    heap = []

    def idx(x, y):
        return y * w + x

    def neighbors_of(i):
        x, y = i % w, i // w
        for dx, dy in _NEIGHBORS:
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                yield idx(nx, ny)

    def enqueue(i):
        if not in_queue[i]:
            in_queue[i] = True
            prop_queue.append(i)

    def set_domain(i, mask):
        if mask == domains[i]:
            return
        if mask == 0:
            raise ContradictionError()
        domains[i] = mask
        version[i] += 1
        for ni in neighbors_of(i):
            enqueue(ni)
        if mask.bit_count() > 1:
            key = _entropy(mask, weight_list, entropy_cache) + noise[i]
            heapq.heappush(heap, (key, i, version[i]))

    for (x, y), mask in domain_cells.items():
        set_domain(idx(x, y), domains[idx(x, y)] & mask)

    for (x, y), ti in pin_cells.items():
        i = idx(x, y)
        set_domain(i, 1 << ti)

    for i in range(cells):
        if domains[i].bit_count() > 1:
            heapq.heappush(heap, (_entropy(domains[i], weight_list, entropy_cache) + noise[i], i, version[i]))

    def propagate():
        while prop_queue:
            i = prop_queue.popleft()
            in_queue[i] = False
            allowed_from_neighbors = full_mask
            for ni in neighbors_of(i):
                union_mask = 0
                for t in _bits(domains[ni]):
                    union_mask |= allowed_masks[t]
                allowed_from_neighbors &= union_mask
            new_mask = domains[i] & allowed_from_neighbors
            if new_mask != domains[i]:
                set_domain(i, new_mask)

    propagate()

    while heap:
        key, i, ver = heapq.heappop(heap)
        if ver != version[i] or domains[i].bit_count() <= 1:
            continue
        tile = _choose_tile(domains[i], weight_list, rng)
        set_domain(i, 1 << tile)
        propagate()

    for i in range(cells):
        if domains[i].bit_count() != 1:
            raise ContradictionError()

    return [domains[i].bit_length() - 1 for i in range(cells)]


def collapse(w, h, tiles, adjacency, *, weights=None, pins=None, domains=None,
             seed=0, max_restarts=20):
    if w <= 0 or h <= 0:
        raise ValueError("w and h must be positive")

    index = {t: i for i, t in enumerate(tiles)}
    n = len(tiles)
    allowed_masks = _build_allowed_masks(tiles, index, adjacency)

    weight_list = [1.0] * n
    if weights:
        for t, wt in weights.items():
            if t not in index:
                raise ValueError(f"unknown tile in weights: {t!r}")
            weight_list[index[t]] = wt

    pin_cells = {}
    for (x, y), t in (pins or {}).items():
        if not (0 <= x < w and 0 <= y < h):
            raise ValueError(f"pin out of bounds: {(x, y)!r}")
        if t not in index:
            raise ValueError(f"unknown tile in pins: {t!r}")
        pin_cells[(x, y)] = index[t]

    domain_cells = {}
    for (x, y), allowed in (domains or {}).items():
        if not (0 <= x < w and 0 <= y < h):
            raise ValueError(f"domain out of bounds: {(x, y)!r}")
        mask = 0
        for t in allowed:
            if t not in index:
                raise ValueError(f"unknown tile in domains: {t!r}")
            mask |= 1 << index[t]
        if mask == 0:
            raise ValueError(f"empty domain at {(x, y)!r}")
        domain_cells[(x, y)] = mask

    entropy_cache = {}
    for attempt in range(max_restarts + 1):
        rng = random.Random(f"{seed}:{attempt}")
        try:
            flat = _solve_once(w, h, n, allowed_masks, weight_list, pin_cells, domain_cells,
                               entropy_cache, rng)
        except ContradictionError:
            continue
        return [[tiles[flat[y * w + x]] for x in range(w)] for y in range(h)]

    raise ContradictionError(f"no solution found after {max_restarts} restarts")
