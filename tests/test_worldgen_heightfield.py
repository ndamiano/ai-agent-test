import numpy as np

from maestro.worldgen.terrain.heightfield import fbm


def test_fbm_lattice_is_not_quantised():
    field = fbm(10.0, 1024, 2, np.random.default_rng(7))
    assert field.shape == (1024, 1024)
    assert field.min() >= -1.0 and field.max() <= 1.0
    # 256 levels per octave leave bicubic shelves; a float lattice leaves none
    assert len(np.unique(field)) > 100_000
