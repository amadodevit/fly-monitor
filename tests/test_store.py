import json
import stat

from fly_monitor.core.config import SourceConfig
from fly_monitor.core.store import Store


def test_secrets_are_kept_apart_from_settings():
    store = Store(":memory:")
    store.add_source(SourceConfig(id="s", plugin="p", name="S", settings={"account": "me", "token": "t0k"}), {"token"})
    (raw,) = store._db.execute("SELECT settings FROM sources").fetchone()
    assert json.loads(raw) == {"account": "me"}
    assert store.secrets.get_all("s") == {"token": "t0k"}
    [loaded] = store.list_sources()
    assert loaded.settings == {"account": "me", "token": "t0k"}


def test_remove_source_removes_secrets():
    store = Store(":memory:")
    store.add_source(SourceConfig(id="s", plugin="p", name="S", settings={"token": "t"}), {"token"})
    store.remove_source("s")
    assert store.list_sources() == [] and store.secrets.get_all("s") == {}


def test_sources_keep_insertion_order():
    store = Store(":memory:")
    for sid in ("b", "a", "c"):
        store.add_source(SourceConfig(id=sid, plugin="p", name=sid), set())
    assert [s.id for s in store.list_sources()] == ["b", "a", "c"]


def test_db_file_is_owner_only(tmp_path):
    path = tmp_path / "state.db"
    Store(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
