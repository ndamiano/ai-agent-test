"""Regenerate the starter tile pack: flat, low-noise, clearly-readable base tiles for every
library class — deliberately humble placeholders that tile perfectly and never read as
wallpaper. Replace any <class>.png with hand-made art at will; this script only exists so the
starters are reproducible. Run: python _starter_gen.py (needs Pillow, already a project dep).
"""
import random
from pathlib import Path

from PIL import Image, ImageDraw

S = 256
HERE = Path(__file__).parent


def _grain(img, rng, amp):
    px = img.load()
    for y in range(S):
        for x in range(S):
            j = rng.randint(-amp, amp)
            r, g, b = px[x, y]
            px[x, y] = (max(0, min(255, r + j)), max(0, min(255, g + j)),
                        max(0, min(255, b + j)))


def _blotches(draw, rng, color, n, rmin, rmax):
    for _ in range(n):
        cx, cy = rng.randrange(S), rng.randrange(S)
        rad = rng.randint(rmin, rmax)
        for ox in (-S, 0, S):
            for oy in (-S, 0, S):
                draw.ellipse((cx + ox - rad, cy + oy - rad, cx + ox + rad, cy + oy + rad),
                             fill=color)


def _speckle(draw, rng, colors, n, size=2):
    for _ in range(n):
        cx, cy = rng.randrange(S), rng.randrange(S)
        c = rng.choice(colors)
        draw.ellipse((cx, cy, cx + size, cy + size), fill=c)


def _rows(draw, base, dark, step, jitter, rng, horizontal=True):
    for i in range(0, S, step):
        w = draw.line
        if horizontal:
            draw.line((0, i, S, i), fill=dark, width=1)
        else:
            draw.line((i, 0, i, S), fill=dark, width=1)


def make(name, base, build):
    rng = random.Random(name)
    img = Image.new("RGB", (S, S), base)
    draw = ImageDraw.Draw(img)
    build(img, draw, rng)
    img.save(HERE / f"{name}.png")
    print(name)


def main():
    def plain(amp=7, blotch=None):
        def b(img, draw, rng):
            if blotch:
                color, n, rmin, rmax = blotch
                _blotches(draw, rng, color, n, rmin, rmax)
            _grain(img, rng, amp)
        return b

    make("grass", (106, 146, 78), plain(8, ((92, 130, 66), 26, 8, 26)))
    make("dirt", (128, 100, 70), plain(8, ((114, 88, 60), 22, 10, 30)))
    make("sand", (214, 192, 145), plain(6, ((202, 180, 132), 18, 12, 34)))
    make("dirt_path", (150, 122, 86), plain(7, ((136, 110, 76), 20, 8, 24)))
    make("marsh", (110, 118, 74), plain(8, ((94, 104, 66), 30, 10, 28)))
    make("dense_growth", (62, 96, 56), plain(9, ((48, 78, 44), 34, 10, 30)))
    make("snow", (233, 238, 243), plain(4, ((222, 229, 238), 14, 12, 34)))
    make("water_shallow", (94, 176, 192), plain(4, ((84, 166, 184), 16, 14, 40)))
    make("water_deep", (44, 92, 128), plain(4, ((38, 82, 116), 16, 14, 40)))
    make("stone_rough", (138, 134, 126), plain(7, ((122, 118, 112), 26, 10, 30)))

    def gravel(img, draw, rng):
        _blotches(draw, rng, (128, 122, 112), 20, 8, 22)
        _speckle(draw, rng, [(112, 106, 98), (160, 154, 144), (134, 126, 114)], 900, 3)
        _grain(img, rng, 5)
    make("gravel", (142, 136, 126), gravel)

    def stone_tile(img, draw, rng):
        dark = (108, 108, 112)
        for y in range(0, S, 64):
            draw.line((0, y, S, y), fill=dark, width=2)
        for row in range(S // 64):
            off = 64 if row % 2 else 0
            for x in range(0, S, 128):
                draw.line(((x + off) % S, row * 64, (x + off) % S, row * 64 + 64),
                          fill=dark, width=2)
        _grain(img, rng, 5)
    make("stone_tile", (150, 150, 154), stone_tile)

    def wood_floor(img, draw, rng):
        dark = (118, 88, 58)
        for y in range(0, S, 32):
            draw.line((0, y, S, y), fill=dark, width=2)
        for row in range(S // 32):
            off = (row * 96 + 40) % S
            draw.line((off, row * 32, off, row * 32 + 32), fill=dark, width=2)
        _grain(img, rng, 6)
    make("wood_floor", (156, 118, 78), wood_floor)

    def roof(img, draw, rng):
        dark = (142, 78, 60)
        for y in range(0, S, 32):
            draw.line((0, y, S, y), fill=dark, width=2)
            for x in range(0, S, 32):
                off = 16 if (y // 32) % 2 else 0
                draw.arc((x + off - 16, y - 8, x + off + 16, y + 24), 200, 340, fill=dark)
        _grain(img, rng, 5)
    make("roof", (172, 96, 74), roof)

    def wall(img, draw, rng):
        dark = (96, 92, 88)
        for y in range(0, S, 32):
            draw.line((0, y, S, y), fill=dark, width=2)
        for row in range(S // 32):
            off = 32 if row % 2 else 0
            for x in range(0, S, 64):
                draw.line(((x + off) % S, row * 32, (x + off) % S, row * 32 + 32),
                          fill=dark, width=2)
        _grain(img, rng, 5)
    make("wall", (128, 124, 118), wall)


if __name__ == "__main__":
    main()
