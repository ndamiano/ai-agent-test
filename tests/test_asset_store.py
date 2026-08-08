import json
import time
from unittest.mock import patch

import pytest

from maestro.codegen import asset_store


@pytest.fixture(autouse=True)
def tmp_store(tmp_path):
    with patch.object(asset_store, "store_dir", lambda: tmp_path):
        yield tmp_path


def _fill(key, phrase="a barrel"):
    asset_store.deposit_subject(key, b"png-subject", phrase, "qwen-2512")
    asset_store.deposit_mesh(key, b"glb-bytes", "trellis2")
    asset_store.deposit_sprite(key, b"png-sprite", "ortho45")


def test_lookup_misses_until_complete():
    key = "barrel__fantasy"
    assert asset_store.lookup(key) is None
    asset_store.deposit_subject(key, b"s", "a barrel", "qwen-2512")
    assert asset_store.lookup(key) is None
    asset_store.deposit_mesh(key, b"m", "trellis2")
    assert asset_store.lookup(key) is None
    asset_store.deposit_sprite(key, b"p", "ortho45")
    entry = asset_store.lookup(key)
    assert entry and entry["key"] == key
    assert entry["meta"]["subject_model"] == "qwen-2512"
    assert entry["meta"]["mesh_model"] == "trellis2"
    assert entry["meta"]["sprite_camera"] == "ortho45"


def test_claim_is_exclusive_and_released_by_sprite_deposit():
    key = "lamp__fantasy"
    assert asset_store.claim(key, "run-a")
    assert not asset_store.claim(key, "run-b")
    _fill(key)
    assert asset_store.claim(key, "run-b")


def test_stale_claim_is_reaped():
    key = "crate__fantasy"
    assert asset_store.claim(key, "run-dead")
    c = asset_store.entry_dir(key) / ".claim"
    held = json.loads(c.read_text())
    held["at"] = time.time() - asset_store.CLAIM_TTL_SECONDS - 1
    c.write_text(json.dumps(held))
    assert asset_store.claim(key, "run-b")


def test_bad_key_rejected():
    assert asset_store.lookup("../escape") is None
    with pytest.raises(ValueError):
        asset_store.entry_dir("UPPER CASE")


def test_evict_and_entries():
    _fill("well__snow", "a stone well")
    _fill("tree__snow", "a pine tree")
    keys = {e["key"] for e in asset_store.entries()}
    assert keys == {"well__snow", "tree__snow"}
    assert asset_store.evict("well__snow")
    assert asset_store.lookup("well__snow") is None
    assert not asset_store.evict("well__snow")


def test_resolve_types_uses_llm_and_styles_key():
    with patch.object(asset_store, "_llm_resolve", return_value={
            "The Salty Dog Inn": {"type": "inn", "phrase": "a wooden inn"}}):
        out = asset_store.resolve_types(["The Salty Dog Inn"], "desert adobe", "run-1")
    r = out["The Salty Dog Inn"]
    assert r["key"] == "inn__desert_adobe"
    assert r["phrase"] == "a wooden inn"


def test_resolve_types_falls_back_to_name():
    with patch.object(asset_store, "_llm_resolve", return_value={}):
        out = asset_store.resolve_types(["Shrine of the White Lotus"], "", "run-1")
    r = out["Shrine of the White Lotus"]
    assert r["key"] == "shrine_of_the_white_lotus"
    assert r["phrase"] == "Shrine of the White Lotus"


def test_resolve_types_dedupes_and_skips_empty():
    with patch.object(asset_store, "_llm_resolve", return_value={}) as m:
        out = asset_store.resolve_types(["Barrel", "Barrel", ""], "s", "run-1")
    assert list(out) == ["Barrel"]
    assert m.call_args[0][0] == ["Barrel"]
