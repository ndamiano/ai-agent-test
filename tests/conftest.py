import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def anyio_backend():
    """asyncio only — the anyio plugin would otherwise run every async test twice, on trio too."""
    return "asyncio"


@pytest.fixture
def app_client():
    """No `with` — a unit test does not want the app's startup handlers (tool registration)."""
    from starlette.testclient import TestClient

    from api.app import app
    return TestClient(app)


@pytest.fixture
def tmp_runs(tmp_path, monkeypatch):
    """Run dirs under this test's tmp dir, for anything that reads or writes runs/<id>/."""
    import maestro.state

    monkeypatch.setattr(maestro.state, "resolve_base_path", lambda input_path=None: tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def isolated_dbs(tmp_path, monkeypatch):
    """Both datastores under this test's tmp dir. Autouse, so a test that never thought about the
    db cannot reach the real one."""
    from auth import store as auth_store
    from db import store as db_store

    monkeypatch.setattr(db_store, "_db_path", lambda: tmp_path / "platform.db")
    monkeypatch.setattr(auth_store, "_db_path", lambda: tmp_path / "auth.db")
    return tmp_path
