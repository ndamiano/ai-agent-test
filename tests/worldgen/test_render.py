import worldgen


def test_render_ascii_contains_every_site_and_key(pirate_recipe):
    world = worldgen.generate(pirate_recipe, seed=1)
    text = worldgen.render_ascii(world)

    assert "Sites:" in text
    assert "Biomes:" in text
    for site in world["sites"]:
        assert site["id"] in text

    w = world["size"]["w"]
    grid_lines = [line for line in text.splitlines() if len(line) == w]
    assert len(grid_lines) == world["size"]["h"]
