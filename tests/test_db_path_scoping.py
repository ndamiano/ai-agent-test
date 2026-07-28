"""The datastores must ignore the execution_context working directory: a tool that repoints it
(the asset stage points it at game/assets/ for ComfyUI output) must not fork an empty db under that dir."""

from pathlib import Path

from auth import store as auth_store
from db import store as db_store
from tools.execution_context import execution_context, resolve_base_path


def test_platform_db_path_ignores_execution_context(tmp_path):
    base = db_store._db_path()
    with execution_context(working_directory=str(tmp_path)):
        assert resolve_base_path() == tmp_path.resolve()   # the context DID repoint tool paths
        assert db_store._db_path() == base                 # the datastore did not follow it


def test_auth_db_path_ignores_execution_context(tmp_path):
    base = auth_store._db_path()
    with execution_context(working_directory=str(tmp_path)):
        assert auth_store._db_path() == base
