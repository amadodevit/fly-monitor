import json
import stat

import pytest

from watchkeep.core.config import SourceConfig
from watchkeep.core.store import Store


def test_secrets_go_to_the_keyring_not_the_database(fake_keyring):
    store = Store(":memory:")
    store.add_source(SourceConfig(id="s", plugin="p", name="S", settings={"account": "me", "token": "t0k"}), {"token"})
    (raw,) = store._db.execute("SELECT settings FROM sources").fetchone()
    assert json.loads(raw) == {"account": "me"}
    assert fake_keyring.entries == {("watchkeep", "s/token"): "t0k"}
    assert store.secrets.get_all("s") == {"token": "t0k"}
    [loaded] = store.list_sources()
    assert loaded.settings == {"account": "me"}  # not read until sync


def test_references_are_not_secret_and_stay_in_the_database(fake_keyring):
    store = Store(":memory:")
    store.add_source(SourceConfig(id="s", plugin="p", name="S", settings={"token": "gh:alice"}), {"token"})
    assert store.list_sources()[0].settings == {"token": "gh:alice"}
    assert fake_keyring.entries == {}


def test_no_keyring_refuses_plaintext_but_accepts_references(fake_keyring):
    fake_keyring.priority = 0  # what the "fail" backend reports
    store = Store(":memory:")
    with pytest.raises(ValueError, match="No keyring"):
        store.add_source(SourceConfig(id="s", plugin="p", name="S", settings={"token": "t0k"}), {"token"})
    assert store.list_sources() == []  # rolled back
    store.add_source(SourceConfig(id="s", plugin="p", name="S", settings={"token": "env:TOKEN"}), {"token"})
    assert store.list_sources()[0].settings == {"token": "env:TOKEN"}


def test_locked_keyring_is_a_clear_error(fake_keyring):
    store = Store(":memory:")
    store.add_source(SourceConfig(id="s", plugin="p", name="S", settings={"token": "t0k"}), {"token"})
    fake_keyring.locked = True
    with pytest.raises(RuntimeError, match="keyring locked"):
        store.secrets.get_all("s")


def legacy_store(tmp_path):
    """A database as older versions left it: secrets in plaintext."""
    store = Store(tmp_path / "state.db")
    store.add_source(SourceConfig(id="s", plugin="p", name="S", settings={"account": "me"}), set())
    store._db.executemany(
        "INSERT INTO source_secrets (source_id, key, value) VALUES (?, ?, ?)",
        [("s", "token", "plain-t0k"), ("s", "backup", "env:BACKUP")],
    )
    store._db.commit()
    return store


def test_migration_moves_secrets_out_of_the_file(tmp_path, fake_keyring):
    store = legacy_store(tmp_path)
    assert store.migrate_secrets() == (2, 0)
    assert store._db.execute("SELECT COUNT(*) FROM source_secrets").fetchone() == (0,)
    assert fake_keyring.entries == {("watchkeep", "s/token"): "plain-t0k"}
    assert store.list_sources()[0].settings == {"account": "me", "backup": "env:BACKUP"}
    store.close()
    assert b"plain-t0k" not in (tmp_path / "state.db").read_bytes()  # vacuumed, not just deleted


def test_migration_without_keyring_leaves_secrets_in_place(tmp_path, fake_keyring):
    fake_keyring.priority = 0
    store = legacy_store(tmp_path)
    assert store.migrate_secrets() == (1, 1)  # the reference moves, the plaintext secret waits
    assert store._db.execute("SELECT value FROM source_secrets").fetchall() == [("plain-t0k",)]


def test_remove_source_removes_secrets(fake_keyring):
    store = Store(":memory:")
    store.add_source(SourceConfig(id="s", plugin="p", name="S", settings={"token": "t"}), {"token"})
    store.remove_source("s")
    assert store.list_sources() == [] and store.secrets.get_all("s") == {}
    assert fake_keyring.entries == {}


def test_sources_keep_insertion_order():
    store = Store(":memory:")
    for sid in ("b", "a", "c"):
        store.add_source(SourceConfig(id=sid, plugin="p", name=sid), set())
    assert [s.id for s in store.list_sources()] == ["b", "a", "c"]


def test_db_file_is_owner_only(tmp_path):
    path = tmp_path / "state.db"
    Store(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_store_migrates_databases_from_before_memory_and_warnings(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE source_state (source_id TEXT PRIMARY KEY, last_sync REAL, last_error TEXT)")
    db.execute("INSERT INTO source_state VALUES ('s', 1000.0, NULL)")
    db.commit()
    db.close()
    store = Store(path)
    assert store.source_state("s") == (1000.0, None)
    assert store.warnings("s") == () and store.memory("s") == {}
    store.replace_snapshot("s", {}, 2000.0, memory={"seen": ["a"]}, warnings=("one feed down",))
    assert store.warnings("s") == ("one feed down",)
    assert store.memory("s") == {"seen": ["a"]}


def test_memory_round_trips_and_goes_with_the_source(tmp_path):
    store = Store(tmp_path / "state.db")
    store.add_source(SourceConfig(id="s", plugin="p", name="S", settings={}), set())
    store.replace_snapshot("s", {}, 1000.0, memory={"feeds": {"u": {"etag": "x"}}})
    store.replace_snapshot("s", {}, 2000.0)  # no memory given: kept as is
    store.close()
    store = Store(tmp_path / "state.db")
    assert store.memory("s") == {"feeds": {"u": {"etag": "x"}}}
    assert store.warnings("s") == ()  # a clean sync clears them
    store.replace_snapshot("s", {}, 3000.0, memory={})
    assert store.memory("s") == {}
    store.replace_snapshot("s", {}, 4000.0, memory={"n": 1})
    store.remove_source("s")
    assert store.memory("s") == {}


def test_databases_without_item_history_get_it_seeded_from_the_snapshot(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE items (source_id TEXT NOT NULL, item_id TEXT NOT NULL, data TEXT NOT NULL, first_seen REAL NOT NULL, PRIMARY KEY (source_id, item_id))")
    shown = {"id": "a", "action": "new post", "title": "A", "url": "https://example.com/a", "repo": "", "author": "", "updated_at": "t"}
    db.execute("INSERT INTO items VALUES ('s', 'a', ?, 500.0)", (json.dumps(shown),))
    db.commit()
    db.close()
    store = Store(path)
    [(source_id, item, first, read_at, present)] = store.history()
    assert (source_id, item.id, first, read_at, present) == ("s", "a", 500.0, None, True)
    store.close()
    Store(path).close()  # a second start doesn't seed again or fail


def test_history_keeps_items_after_they_leave_and_first_seen_on_update():
    from watchkeep.core.models import Item

    def it(title):
        return Item(id="a", action="new", title=title, url="u", repo="", author="", updated_at=title)

    store = Store(":memory:")
    store.replace_snapshot("s", {"a": (it("v1"), 100.0)}, 100.0)
    store.replace_snapshot("s", {"a": (it("v2"), 100.0)}, 200.0)
    store.replace_snapshot("s", {}, 300.0)
    store.replace_snapshot("s", {"a": (it("v3"), 400.0)}, 400.0)  # back: snapshot restarts first_seen, history doesn't
    [(_, item, first, _, present)] = store.history()
    assert (item.title, first, present) == ("v3", 100.0, True)
    assert store.set_read("s", "a", 450.0) and store.read_items("s") == {"a"}
    assert not store.set_read("s", "missing", 450.0)
    store.remove_source("s")
    assert store.history() == []
