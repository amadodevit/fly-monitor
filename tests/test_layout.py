import time

from watchkeep.core.engine import PluginInfo
from watchkeep.core.models import HealthView, Item, ItemView, SourceView, State
from watchkeep.core.panels import Panel, PanelFilter, all_panel
from watchkeep.core.plugin import Metric
from watchkeep.ui.layout import PRESETS, View, build, empty_message, filter_from_group, sources_in, tab_failing_count

NOW = 1_000_000.0
QUEUE_METRICS = (
    Metric("size", "Messages waiting", short="waiting"),
    Metric("consumers", "Consumers"),
    Metric("enqueued", "Enqueued", counter=True, short="in"),
    Metric("dequeued", "Dequeued", counter=True, short="out"),
)


def info(name, title, kind="samples", metrics=()):
    return PluginInfo(name, "http", (), False, False, kind, 60, None, metrics, (), title)


PLUGINS = {
    "activemq": info("activemq", "ActiveMQ queues", metrics=QUEUE_METRICS),
    "website": info("website", "Websites", metrics=(Metric("status", "Status"), Metric("response_ms", "ms", short="ms"))),
    "github": info("github", "GitHub", kind="items"),
}


def queue(key, state="ok", acknowledged=False, size=0, fired=()):
    return HealthView(
        key=key, state=state, since=NOW - 180, summary="", failures=0, window=3, checked_at=NOW, url="",
        acknowledged=acknowledged, values={"size": size, "consumers": 1}, growth={"enqueued": 10, "dequeued": 10}, fired=fired,
    )


def source(id_, plugin, name, *, health=(), items=(), client="", environment="", error=None, tags=()):
    return SourceView(
        id=id_, plugin=plugin, name=name, tags=tuple(tags), interval=60, last_sync=NOW - 60, last_error=error,
        syncing=False, items=tuple(items), health=tuple(health), client=client, environment=environment,
    )


def review(n):
    item = Item(id=n, action="review requested", title=f"PR {n}", url=f"https://x/{n}", repo="acme/widgets", author="alice", updated_at="2026-01-01T00:00:00+00:00")
    return ItemView(item=item, first_seen=NOW, is_new=False)


def text(rows):
    return [" | ".join(str(c) for c in r.cells).rstrip(" |") for r in rows]


def build_rows(*sources, view=None):
    return build(State(1, tuple(sources)), PLUGINS, view or View(), now=lambda: NOW)


def test_healthy_queue_source_is_one_line_with_totals():
    broker = source("b", "activemq", "Prod broker", health=[queue("orders", size=3), queue("billing", size=2)], client="Acme")
    rows = build_rows(broker)
    assert [r.kind for r in rows] == ["group", "group", "source"]
    assert text(rows)[1].split(" | ")[2:6] == ["waiting", "consumers", "in", "out"]  # kind header labels columns
    assert text(rows)[2] == "    ▸ Prod broker | 2 ok | 5 | 2 | +20 | +20 | checked 1m ago"


def test_trouble_shows_without_unfolding_and_comes_first():
    broker = source("b", "activemq", "Prod broker", health=[queue("a"), queue("z", "failing", fired=("Stuck",)), queue("m")])
    rows = build_rows(broker)
    assert [r.kind for r in rows][2:] == ["source", "health", "more"]
    assert rows[3].ref == "z" and str(rows[3].cells[-1]) == "failing 3m · Stuck"
    assert str(rows[4].cells[0]).strip() == "+2 ok"
    expanded = build_rows(broker, view=View(expanded={"b": True}))
    assert [r.ref for r in expanded if r.kind == "health"] == ["z", "a", "m"]


def test_website_is_a_single_line():
    site = HealthView(key="https://example.com", state="ok", since=0, summary="200 · 84 ms", failures=0, window=10,
                      checked_at=NOW, url="https://example.com", values={"status": 200, "response_ms": 84})
    rows = build_rows(source("w", "website", "Example", health=[site]))
    assert [r.kind for r in rows] == ["group", "group", "source"]
    assert rows[2].ref == "https://example.com" and rows[2].url == "https://example.com"
    assert [str(c) for c in rows[2].cells[2:4]] == ["200", "84"]


def test_items_and_idle_accounts():
    busy = source("w", "github", "work", items=[review("1")], client="Acme")
    idle = source("p", "github", "home", client="Acme")
    rows = build_rows(busy, idle)
    assert text(rows)[1].split(" | ")[2:5] == ["repo", "by", "updated"]
    assert [r.kind for r in rows] == ["group", "group", "source", "item", "source"]
    assert "nothing needs you" in text(rows)[4]


def test_groups_by_client_then_kind_with_unset_last_and_red_when_failing():
    rows = build_rows(
        source("a", "github", "acct", client="Zeta"),
        source("b", "activemq", "broker", health=[queue("q", "failing")], client="Acme"),
        source("c", "github", "other"),
    )
    groups = [str(r.cells[0]).strip() for r in rows if r.kind == "group"]
    assert groups == ["▾ Acme", "▾ ActiveMQ queues", "▾ Zeta", "▾ GitHub", "▾ No client", "▾ GitHub"]
    assert "red" in str(rows[0].cells[0].style) and "1 failing" in str(rows[0].cells[1])


def test_presets_and_folded_groups():
    sources = [source("a", "github", "acct", client="Acme", environment="prod"), source("b", "github", "x", environment="prod")]
    by_env = build_rows(*sources, view=View(levels=dict(PRESETS)["Environment › Kind"]))
    assert [str(r.cells[0]).strip() for r in by_env if r.kind == "group"] == ["▾ prod", "▾ GitHub"]
    flat = build_rows(*sources, view=View(levels=()))
    assert [r.kind for r in flat] == ["source", "source"]
    folded = build_rows(*sources, view=View(folded_groups={"grp:Acme"}))
    assert [str(r.cells[0]).strip() for r in folded][:2] == ["▸ Acme", "▾ No client"]


# -- panels --------------------------------------------------------------------


def panel(**rules):
    return Panel(id="p1", name="P", filter=PanelFilter(**{k: tuple(v) for k, v in rules.items()}))


PANEL_SOURCES = (
    source("a", "github", "work", client="acme", environment="prod"),
    source("b", "activemq", "broker", health=[queue("q", "failing")], client="globex", environment="prod"),
    source("c", "website", "blog", tags=("personal",)),
    source("d", "activemq", "staging broker", health=[queue("q")], client="acme", environment="staging"),
)


def test_panel_sources_in_keeps_order_and_version():
    state = State(7, PANEL_SOURCES)
    assert [s.id for s in sources_in(panel(clients=["acme"]), state).sources] == ["a", "d"]
    assert [s.id for s in sources_in(panel(tags=["personal"], picks=["b"]), state).sources] == ["b", "c"]
    assert sources_in(panel(clients=["acme"]), state).version == 7
    assert sources_in(all_panel(), state).sources == PANEL_SOURCES


def test_panel_grouping_still_works_on_the_subset():
    rows = build(sources_in(panel(environments=["prod"]), State(1, PANEL_SOURCES)), PLUGINS, View(), now=lambda: NOW)
    groups = [str(r.cells[0]).strip() for r in rows if r.kind == "group"]
    assert groups == ["▾ acme", "▾ GitHub", "▾ globex", "▾ ActiveMQ queues"]


def test_panel_tab_failing_count_counts_failing_rows_and_errors_in_the_panel_only():
    sources = (
        source("x", "activemq", "broker", client="acme",
               health=[queue("a", "failing"), queue("b", "failing", acknowledged=True), queue("c")]),
        source("y", "github", "work", client="acme", error="401 unauthorized"),
        source("z", "activemq", "other", client="globex", health=[queue("a", "failing")]),
    )
    state = State(1, sources)
    assert tab_failing_count(panel(clients=["acme"]), state) == 2  # one unacknowledged failing row + one error
    assert tab_failing_count(panel(clients=["globex"]), state) == 1
    assert tab_failing_count(all_panel(), state) == 3
    assert tab_failing_count(panel(tags=["nothing"]), state) == 0


def test_panel_empty_message_names_the_filter():
    assert empty_message(panel(clients=["acme"])) == "No sources match client = acme"
    assert empty_message(panel(clients=["acme", "globex"], environments=["prod"])) == (
        "No sources match client = acme or globex, environment = prod"
    )
    assert empty_message(panel(tags=["personal"], plugins=["website"])) == "No sources match tag = personal, type = website"
    assert empty_message(panel(picks=["gone"])) == "No sources match 1 hand-picked source"
    assert empty_message(panel()) == "No sources match: this panel has no rules yet"
    assert empty_message(all_panel()) == "No sources yet, press a to add one"


def test_panel_filter_from_group_header():
    client_kind = dict(PRESETS)["Client › Kind"]
    assert filter_from_group("grp:acme", client_kind, PLUGINS) == ("acme", PanelFilter(clients=("acme",)))
    assert filter_from_group("grp:acme\x1fActiveMQ queues", client_kind, PLUGINS) == (
        "acme › ActiveMQ queues", PanelFilter(clients=("acme",), plugins=("activemq",)),
    )
    assert filter_from_group("grp:prod", dict(PRESETS)["Environment › Kind"], PLUGINS) == ("prod", PanelFilter(environments=("prod",)))
    assert filter_from_group("grp:Websites", dict(PRESETS)["Kind"], PLUGINS) == ("Websites", PanelFilter(plugins=("website",)))
    assert filter_from_group("grp:disk", dict(PRESETS)["Kind"], PLUGINS) == ("disk", PanelFilter(plugins=("disk",)))  # unknown plugin: title is its name


def test_panel_filter_from_group_refuses_what_a_rule_cannot_express():
    client_kind = dict(PRESETS)["Client › Kind"]
    assert filter_from_group("grp:No client", client_kind, PLUGINS) is None
    assert filter_from_group("hdr:a", client_kind, PLUGINS) is None


def test_panel_filter_from_a_real_group_row_matches_its_members():
    rows = build(State(1, PANEL_SOURCES), PLUGINS, View(), now=lambda: NOW)
    key = next(r.key for r in rows if r.kind == "group" and str(r.cells[0]).strip() == "▾ acme")
    _, f = filter_from_group(key, View().levels, PLUGINS)
    assert [s.id for s in sources_in(Panel("x", "acme", f), State(1, PANEL_SOURCES)).sources] == ["a", "d"]
