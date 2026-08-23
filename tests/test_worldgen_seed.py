"""A seed keyed by name must survive a process restart, or no world build reproduces."""
import zlib

from maestro.worldgen.seed import seed_for


def test_the_same_name_seeds_the_same_and_is_its_crc32():
    assert seed_for("oak tree") == seed_for("oak tree") == zlib.crc32(b"oak tree")
    assert seed_for("meadow", 3) == zlib.crc32(b"meadow/3")
    assert seed_for("a") != seed_for("b")
