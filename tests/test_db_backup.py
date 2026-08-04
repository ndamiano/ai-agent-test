import gzip
import sqlite3
import time
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tools import db_backup, s3


class FakeBucket:
    def __init__(self):
        self.objects = {}

    def put(self, key, data):
        self.objects[key] = data


@pytest.fixture
def wired(tmp_path, monkeypatch):
    fake = FakeBucket()
    monkeypatch.setattr(s3, "configured", lambda: True)
    monkeypatch.setattr(s3, "put", fake.put)

    def make_db(name):
        p = tmp_path / f"{name}.db"
        conn = sqlite3.connect(p)
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('alive')")
        conn.commit()
        conn.close()
        return p

    paths = [("platform", make_db("platform")), ("auth", make_db("auth"))]
    monkeypatch.setattr(db_backup, "_db_paths", lambda: paths)
    monkeypatch.setattr(db_backup, "_dirty_at", None)
    return fake, paths


def _load(data: bytes, tmp_path: Path) -> sqlite3.Connection:
    p = tmp_path / "restored.db"
    p.write_bytes(gzip.decompress(data))
    return sqlite3.connect(p)


def test_snapshot_uploads_a_restorable_copy_of_both_dbs(wired, tmp_path):
    fake, _ = wired
    assert db_backup.snapshot_all() == 2
    for name in ("platform", "auth"):
        conn = _load(fake.objects[f"db/{name}/latest.db.gz"], tmp_path)
        assert conn.execute("SELECT v FROM t").fetchone()[0] == "alive"
        conn.close()


def test_daily_snapshot_also_writes_the_dated_key(wired):
    fake, _ = wired
    db_backup.snapshot_all(daily=True)
    dated = [k for k in fake.objects if k.startswith("db/platform/2")]
    assert dated and dated[0].endswith(".db.gz")


def test_a_missing_db_is_skipped_not_fatal(wired):
    fake, paths = wired
    paths[1] = ("auth", Path("/nowhere/auth.db"))
    assert db_backup.snapshot_all() == 1


def test_failed_upload_never_raises(wired, monkeypatch):
    fake, _ = wired
    monkeypatch.setattr(s3, "put", lambda k, d: (_ for _ in ()).throw(RuntimeError("bucket down")))
    assert db_backup.snapshot_all() == 0


def test_dirty_flag_fires_after_the_debounce_and_only_once(wired, monkeypatch):
    db_backup.mark_dirty()
    now = time.time()
    assert db_backup._take_dirty(now) is False                       # inside the debounce
    later = now + db_backup.DIRTY_DEBOUNCE_SECONDS + 1
    assert db_backup._take_dirty(later) is True
    assert db_backup._take_dirty(later + 1) is False                 # consumed


def test_money_and_account_writes_mark_dirty(tmp_path, monkeypatch):
    import auth.store as auth_store
    monkeypatch.setattr(auth_store, "_db_path", lambda: tmp_path / "auth.db")
    monkeypatch.setattr(db_backup, "_dirty_at", None)
    u = auth_store.create_user("snapshot-test", "pw-pass1234", email="snapshot-test@example.com")
    assert db_backup._dirty_at is not None

    monkeypatch.setattr(db_backup, "_dirty_at", None)
    auth_store.grant(u.id, 3, "admin_grant")
    assert db_backup._dirty_at is not None
