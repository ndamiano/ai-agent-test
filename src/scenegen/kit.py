"""Kit-assembled buildings: procedural wall/roof fills + reused part sprites, any footprint, the
door always on a knowable cell.

Monolithic AI building sprites measured out: no knowable door cell, no resizing, no part reuse —
"hope for the best" per building. Assembly inverts that: one door sprite and one window sprite
repeat across every building (the affordance a player learns once), and `assemble` RETURNS the
door's grid cell so game logic wires entrances mechanically. Restyle = swap the kit, never
img2img the assembled building (0.6 denoise keeps the old style, 0.8 dissolves the structure —
there is no sweet spot).
"""

from dataclasses import dataclass
from typing import Optional, Tuple

from PIL import Image, ImageDraw

Color = Tuple[int, int, int]


@dataclass
class BuildingKit:
    door: Image.Image
    window: Image.Image
    wall: Color = (238, 226, 204)
    wall_seam: Color = (230, 216, 192)
    trim: Color = (122, 94, 70)
    roof: Color = (178, 62, 48)
    roof_tint: Optional[Color] = None      # ridge/eave accent (neon trim); None derives from roof
    eave_spread: int = 0                   # px each tile row widens: >0 fakes curved eaves
    roof_tile_h: int = 12
    extra: Optional[Image.Image] = None    # chimney / AC vent, pasted at the roof's far corner


def assemble(pil: Image.Image, kit: BuildingKit, gx: int, gy: int, wc: int, hw: int,
             cell: int = 48) -> Tuple[int, int]:
    """Draw a building with its wall top-left at cell (gx, gy), `wc` cells wide, `hw` wall cells
    tall; the roof extends above. Returns the door cell (x, y) — y is the walkable cell the door
    opens onto."""
    x0, y0 = gx * cell, gy * cell
    wpx, wall_h = wc * cell, hw * cell
    roof_h = int(wc * cell * 0.32) + 22
    d = ImageDraw.Draw(pil)

    d.rectangle([x0, y0, x0 + wpx - 1, y0 + wall_h - 1], fill=kit.wall)
    for i in range(0, wpx, 12):
        d.line([(x0 + i, y0), (x0 + i, y0 + wall_h)], fill=kit.wall_seam, width=1)
    d.rectangle([x0, y0 + wall_h - 6, x0 + wpx - 1, y0 + wall_h - 1], fill=kit.trim)

    ry0, ry1 = y0 - roof_h, y0
    n = max(1, roof_h // kit.roof_tile_h)
    accent = kit.roof_tint or tuple(min(255, int(c * 1.18)) for c in kit.roof)
    for i in range(n):
        ry = ry0 + i * kit.roof_tile_h
        spread = 8 + int(kit.eave_spread * (i / max(1, n - 1)) ** 1.6)
        shade = tuple(int(c * (1.0 - 0.028 * i)) for c in kit.roof)
        d.rectangle([x0 - spread, ry, x0 + wpx + spread, min(ry + kit.roof_tile_h, ry1)],
                    fill=shade)
        d.line([(x0 - spread, ry), (x0 + wpx + spread, ry)],
               fill=tuple(int(c * 0.8) for c in kit.roof), width=2)
    d.rectangle([x0 - 4, ry0, x0 + wpx + 4, ry0 + 7], fill=accent)
    d.rectangle([x0 - 8 - kit.eave_spread, ry1 - 6, x0 + wpx + 8 + kit.eave_spread, ry1],
                fill=tuple(int(c * 0.55) for c in kit.roof))

    dc = wc // 2
    door = kit.door
    pil.paste(door, (x0 + dc * cell + (cell - door.width) // 2, y0 + wall_h - door.height), door)
    for c in range(wc):
        if abs(c - dc) >= 1:
            win = kit.window
            pil.paste(win, (x0 + c * cell + (cell - win.width) // 2,
                            y0 + wall_h - win.height - 14), win)
    if kit.extra is not None:
        pil.paste(kit.extra, (x0 + wpx - cell + 4, ry0 + 6), kit.extra)

    shadow = Image.new("RGBA", (wpx + 16, 10), (0, 0, 0, 80))
    pil.paste(shadow, (x0 - 8, y0 + wall_h), shadow)
    return (gx + dc, gy + hw)
