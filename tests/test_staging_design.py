"""The spec's life in the game folder: seeded as design/design.md, counted as ours rather than
the model's, and kept out of what a player's browser can fetch."""

from maestro.codegen import staging

SPEC = "# BUILD SPEC\n\n# 0. SCOPE\n\nbody\n\n# A. SANITY\n\nchecks\n"


def test_the_spec_and_the_tool_docs_are_seeded_beside_the_game(tmp_path):
    staging.spec_path(tmp_path).write_text(SPEC, encoding="utf-8")

    staging.seed_vendor(tmp_path)

    game = staging.game_dir(tmp_path)
    assert (game / "design" / "design.md").read_text() == SPEC
    assert (game / "docs" / "generate_media.md").is_file()
    assert (game / "docs" / "compose_world.md").is_file()


def test_seeds_are_not_the_models_work_and_do_not_ship(tmp_path, monkeypatch):
    staging.spec_path(tmp_path).write_text(SPEC, encoding="utf-8")
    staging.seed_vendor(tmp_path)
    assert staging.has_authored_files(tmp_path) is False

    game = staging.game_dir(tmp_path)
    (game / "index.html").write_text("<html></html>")
    assert staging.has_authored_files(tmp_path) is True
    monkeypatch.setattr(staging, "RUNTIME_DIR", tmp_path / "runtime")

    staging.stage_for_play(tmp_path, "slug")

    staged = tmp_path / "runtime" / "games" / "slug"
    assert (staged / "index.html").is_file()
    assert not (staged / "design").exists() and not (staged / "docs").exists()
