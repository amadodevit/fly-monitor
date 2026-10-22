import asyncio

import pytest

from fly_monitor.core.config import SourceConfig
from fly_monitor.core.engine import ERROR_RETRY, Core
from fly_monitor.core.health import Rule
from fly_monitor.core.models import Item, Sample
from fly_monitor.core.plugin import Candidate, Choice, Setting
from fly_monitor.core.registry import discover
from fly_monitor.core.store import Store


def item(id_):
    return Item(id=id_, action="review requested", title=id_, url=f"https://x/{id_}", repo="a/b", author="z", updated_at="t")


class FakePlugin:
    name = "fake"
    connection = "http"
    settings = (Setting("account", "Account"), Setting("token", "Token", secret=True, required=False, default="none"))

    def __init__(self):
        self.next = []
        self.fail = False
        self.seen_settings = None

    def sync(self, ctx):
        self.seen_settings = ctx.settings
        if self.fail:
            raise RuntimeError("boom")
        return list(self.next)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def setup():
    plugin, clock = FakePlugin(), Clock()
    core = Core({"fake": plugin}, Store(":memory:"), clock=clock)
    core.add_source("fake", "S1", {"account": "me"}, interval=600, source_id="s1")
    return core, plugin, clock


def items_of(core):
    return {v.item.id: v for v in core.get_state().sources[0].items}


def test_sync_lifecycle_keeps_first_seen_and_flags_new(setup):
    core, plugin, clock = setup
    plugin.next = [item("a")]
    asyncio.run(core.sync_now())
    assert items_of(core)["a"].is_new

    clock.now += 700
    plugin.next = [item("a"), item("b")]
    asyncio.run(core.sync_now())
    seen = items_of(core)
    assert seen["a"].first_seen == 1000.0 and not seen["a"].is_new
    assert seen["b"].is_new

    clock.now += 700
    plugin.next = [item("b")]  # "a" was reviewed
    asyncio.run(core.sync_now())
    assert set(items_of(core)) == {"b"}

    clock.now += 700
    plugin.next = [item("a"), item("b")]  # "a" re-requested
    asyncio.run(core.sync_now())
    assert items_of(core)["a"].is_new and items_of(core)["a"].first_seen == clock.now


def test_failed_sync_keeps_items_and_reports_error(setup):
    core, plugin, clock = setup
    plugin.next = [item("a")]
    asyncio.run(core.sync_now())
    plugin.fail = True
    asyncio.run(core.sync_now())
    source = core.get_state().sources[0]
    assert source.last_error == "boom"
    assert [v.item.id for v in source.items] == ["a"]


def test_due_respects_interval_and_error_retry(setup):
    core, plugin, clock = setup
    assert core._is_due("s1")  # never synced
    asyncio.run(core.sync_now())
    assert not core._is_due("s1")
    clock.now += 600
    assert core._is_due("s1")

    core.add_source("fake", "S2", {"account": "me"}, interval=3600, source_id="s2")
    plugin.fail = True
    asyncio.run(core.sync_now("s2"))
    clock.now += ERROR_RETRY
    assert core._is_due("s2")  # retried before the 1h interval


def test_add_source_validates_and_fills_defaults(setup):
    core, plugin, _ = setup
    with pytest.raises(ValueError, match="no plugin"):
        core.add_source("nope", "x", {})
    with pytest.raises(ValueError, match="missing: Account"):
        core.add_source("fake", "x", {"account": ""})
    with pytest.raises(ValueError, match="name is required"):
        core.add_source("fake", "  ", {"account": "me"})
    asyncio.run(core.sync_now("s1"))
    assert plugin.seen_settings == {"account": "me", "token": "none"}  # default filled in


def test_ids_are_slugs_and_unique(setup):
    core, _, _ = setup
    assert core.add_source("fake", "Acme Work!", {"account": "me"}) == "acme-work"
    assert core.add_source("fake", "Acme Work", {"account": "me"}) == "acme-work-2"


def test_remove_source_drops_it_and_its_history(setup):
    core, plugin, _ = setup
    plugin.next = [item("a")]
    asyncio.run(core.sync_now())
    core.remove_source("s1")
    assert core.get_state().sources == ()
    core.add_source("fake", "S1", {"account": "me"}, source_id="s1")
    assert core.get_state().sources[0].items == ()  # no leftovers under a reused id


def test_source_with_uninstalled_plugin_is_shown_not_run():
    store = Store(":memory:")
    store.add_source(SourceConfig(id="old", plugin="gone", name="Old"), secret_keys=set())
    core = Core({}, store)
    [view] = core.get_state().sources
    assert view.last_error == "plugin 'gone' is not installed"
    asyncio.run(core.sync_now())  # must not crash
    with pytest.raises(RuntimeError, match="not installed"):
        core.test_connection("old")


def test_import_config_runs_once():
    core = Core({"fake": FakePlugin()}, Store(":memory:"))
    sources = [SourceConfig(id="gh-a", plugin="fake", name="A", interval=60, tags=("work",), settings={"account": "a"})]
    assert core.import_config(sources) == 1
    [view] = core.get_state().sources
    assert (view.id, view.interval, view.tags) == ("gh-a", 60, ("work",))
    core.remove_source("gh-a")
    assert core.import_config(sources) == 0  # marker set: deleting everything doesn't re-import


def test_github_plugin_is_discovered_via_entry_point():
    assert "github" in discover()


class DetectingPlugin(FakePlugin):
    name = "det"
    identity = ("account",)
    settings = (
        Setting("account", "Account"),
        Setting("watch", "Watch", choices=(Choice("a", "A"), Choice("b", "B", default=False))),
    )

    def detect(self):
        return [Candidate("one", {"account": "one"}), Candidate("two", {"account": "two"})]


def test_detect_marks_already_added_by_identity():
    core = Core({"det": DetectingPlugin()}, Store(":memory:"))
    sid = core.add_source("det", "Mine", {"account": "two"})
    found = core.detect_sources("det")
    assert [(f.name, f.existing_id) for f in found] == [("one", None), ("two", sid)]


def test_choice_defaults_and_empty_selection():
    plugin = DetectingPlugin()
    core = Core({"det": plugin}, Store(":memory:"))
    core.add_source("det", "x", {"account": "x"})  # watch omitted -> defaults
    asyncio.run(core.sync_now())
    assert plugin.seen_settings["watch"] == ["a"]
    with pytest.raises(ValueError, match="missing: Watch"):
        core.add_source("det", "y", {"account": "y", "watch": []})  # user unticked everything


class SamplePlugin:
    name = "probe"
    connection = "http"
    kind = "samples"
    default_rule = Rule(window=4, fail_after=2, recover_after=2)
    settings = (Setting("url", "URL"),)

    def __init__(self):
        self.ok = True

    def sync(self, ctx):
        return [Sample(key=ctx.settings["url"], ok=self.ok, summary="200" if self.ok else "500", values={}, url=ctx.settings["url"])]


def test_samples_drive_health_with_stored_rule():
    plugin, clock = SamplePlugin(), Clock()
    store = Store(":memory:")
    core = Core({"probe": plugin}, store, clock=clock)
    sid = core.add_source("probe", "site", {"url": "https://x"})
    assert store.list_sources()[0].rule == {"window": 4, "fail_after": 2, "recover_after": 2}

    plugin.ok = False
    for _ in range(2):
        clock.now += 1
        asyncio.run(core.sync_now(sid))
    [h] = core.get_state().sources[0].health
    assert (h.state, h.summary, h.failures, h.window, h.url) == ("failing", "500", 2, 4, "https://x")

    plugin.ok = True
    for _ in range(2):
        clock.now += 1
        asyncio.run(core.sync_now(sid))
    [h] = core.get_state().sources[0].health
    assert h.state == "ok" and h.failures == 0

    # survives a restart: health comes from disk
    [h2] = Core({"probe": plugin}, store, clock=clock).get_state().sources[0].health
    assert h2 == h


def test_custom_rule_is_validated_and_kept():
    core = Core({"probe": SamplePlugin()}, Store(":memory:"))
    with pytest.raises(ValueError, match="within only"):
        core.add_source("probe", "a", {"url": "u"}, rule={"window": 3, "fail_after": 5, "recover_after": 1})
    sid = core.add_source("probe", "b", {"url": "u"}, rule={"window": 20, "fail_after": 5, "recover_after": 1})
    assert core._all[sid].rule["window"] == 20


def test_item_sources_have_no_rule(setup):
    core, _, _ = setup
    assert core._all["s1"].rule is None


def test_store_migrates_databases_without_rule_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE sources (id TEXT PRIMARY KEY, plugin TEXT NOT NULL, name TEXT NOT NULL, interval INTEGER NOT NULL, tags TEXT NOT NULL, settings TEXT NOT NULL, position INTEGER NOT NULL)")
    db.execute("INSERT INTO sources VALUES ('gh', 'github', 'GH', 3600, '[]', '{}', 0)")
    db.commit()
    db.close()
    [source] = Store(path).list_sources()
    assert source.id == "gh" and source.rule is None


def test_secret_reaches_the_plugin_at_sync_and_a_locked_keyring_retries(fake_keyring):
    plugin, clock = FakePlugin(), Clock()
    core = Core({"fake": plugin}, Store(":memory:"), clock=clock)
    core.add_source("fake", "S1", {"account": "me", "token": "t0k"}, interval=3600, source_id="s1")
    asyncio.run(core.sync_now())
    assert plugin.seen_settings["token"] == "t0k"

    fake_keyring.locked = True
    clock.now += 3600
    asyncio.run(core.sync_now())
    assert "keyring locked" in core.get_state().sources[0].last_error
    clock.now += ERROR_RETRY
    assert core._is_due("s1")  # retried within 5 minutes, not an hour
