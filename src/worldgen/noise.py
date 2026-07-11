def _hash01(ix: int, iy: int, seed: int) -> float:
    h = (ix * 374761393 + iy * 668265263 + seed * 2147483647) & 0xFFFFFFFF
    h = (h ^ (h >> 13)) * 1274126177 & 0xFFFFFFFF
    h = (h ^ (h >> 16)) & 0xFFFFFFFF
    return h / 0xFFFFFFFF


def _fade(t: float) -> float:
    return t * t * t * (t * (t * 6 - 15) + 10)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def value_noise(x: float, y: float, seed: int) -> float:
    """Lattice value noise, bilinear-interpolated with a quintic fade. Range [-1, 1]."""
    ix, iy = int(x // 1), int(y // 1)
    fx, fy = x - ix, y - iy

    v00 = _hash01(ix, iy, seed)
    v10 = _hash01(ix + 1, iy, seed)
    v01 = _hash01(ix, iy + 1, seed)
    v11 = _hash01(ix + 1, iy + 1, seed)

    u, v = _fade(fx), _fade(fy)
    top = _lerp(v00, v10, u)
    bottom = _lerp(v01, v11, u)
    return _lerp(top, bottom, v) * 2 - 1


def fbm(x: float, y: float, seed: int, octaves: int = 5, persistence: float = 0.5,
        lacunarity: float = 2.0) -> float:
    """Fractal Brownian motion over value_noise. Range approx [-1, 1]."""
    total = 0.0
    amplitude = 1.0
    max_amplitude = 0.0
    freq = 1.0
    for octave in range(octaves):
        total += value_noise(x * freq, y * freq, seed + octave * 1013) * amplitude
        max_amplitude += amplitude
        amplitude *= persistence
        freq *= lacunarity
    return total / max_amplitude
