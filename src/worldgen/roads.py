import heapq

from .errors import PlacementError

_DIRS = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
         (1, 1, 2 ** 0.5), (1, -1, 2 ** 0.5), (-1, 1, 2 ** 0.5), (-1, -1, 2 ** 0.5)]

_RIVER_CROSSING_COST = 8.0


def _cell_cost(x, y, elevation, water, river_cells):
    if water[y][x]:
        return None
    cost = 1.0 + elevation[y][x] * 4.0
    if (x, y) in river_cells:
        cost += _RIVER_CROSSING_COST
    return cost


def _astar(start, goal, ctx, river_cells):
    w, h = ctx.w, ctx.h
    sx, sy = start
    gx, gy = goal
    if ctx.water[sy][sx] or ctx.water[gy][gx]:
        return None

    def heuristic(x, y):
        return ((x - gx) ** 2 + (y - gy) ** 2) ** 0.5

    open_heap = [(heuristic(sx, sy), 0.0, (sx, sy))]
    came_from = {}
    g_score = {(sx, sy): 0.0}
    visited = set()

    while open_heap:
        _, g, node = heapq.heappop(open_heap)
        if node in visited:
            continue
        visited.add(node)
        if node == (gx, gy):
            path = [node]
            while node in came_from:
                node = came_from[node]
                path.append(node)
            path.reverse()
            return path

        x, y = node
        for dx, dy, step_cost in _DIRS:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < w and 0 <= ny < h):
                continue
            cell_cost = _cell_cost(nx, ny, ctx.elevation, ctx.water, river_cells)
            if cell_cost is None:
                continue
            tentative = g + step_cost * cell_cost
            neighbor = (nx, ny)
            if tentative < g_score.get(neighbor, float("inf")):
                g_score[neighbor] = tentative
                came_from[neighbor] = node
                heapq.heappush(open_heap, (tentative + heuristic(nx, ny), tentative, neighbor))

    return None


def _mst_edges(points):
    n = len(points)
    in_tree = [False] * n
    in_tree[0] = True
    edges = []
    remaining = set(range(1, n))
    while remaining:
        best = None
        for i in range(n):
            if not in_tree[i]:
                continue
            for j in remaining:
                d = (points[i][0] - points[j][0]) ** 2 + (points[i][1] - points[j][1]) ** 2
                if best is None or d < best[0]:
                    best = (d, i, j)
        _, i, j = best
        edges.append((i, j))
        in_tree[j] = True
        remaining.discard(j)
    return edges


def connect(settlements, ctx, river_cells=frozenset()):
    """Roads link settlements that share a landmass; settlements on different islands
    connect by water, which is open by construction. A road may cross a river
    (surcharged, not forbidden, so crossings are narrow) — each such cell is
    reported as a bridge."""
    by_island = {}
    for s in settlements:
        by_island.setdefault(ctx.component_id[s["y"]][s["x"]], []).append(s)

    roads = []
    bridges = set()
    for group in by_island.values():
        if len(group) < 2:
            continue
        points = [(s["x"], s["y"]) for s in group]
        for i, j in _mst_edges(points):
            path = _astar(points[i], points[j], ctx, river_cells)
            if path is None:
                raise PlacementError(
                    f"no road between {group[i]['id']!r} and {group[j]['id']!r}")
            roads.append([list(c) for c in path])
            bridges.update(c for c in path if c in river_cells)
    return roads, [list(c) for c in sorted(bridges)]
