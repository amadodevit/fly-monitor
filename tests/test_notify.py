import asyncio

import pytest

from fly_monitor.core.engine import Core
from fly_monitor.core.health import Rule
from fly_monitor.core.models import Item, Sample
from fly_monitor.core.notify import FAILING, NEW_ITEM, DesktopChannel, Event
from fly_monitor.core.plugin import Setting
from fly_monitor.core.store import Store


def item(id_):
    return Item(id=id_, action="review requested", title=f"PR {id_}", url=f"https://x/{id_}", repo="a/b", author="z", updated_at="t")


class ItemsPlugin:
    name = "items"
    connection = "http"
    settings = (Setting("account", "Account"),)

    def __init__(self):
        self.next = []

    def sync(self, ctx):
        return list(self.next)


class ProbePlugin:
    name = "probe"
    connection = "http"
    kind = "samples"
    default_rule = Rule(window=4, fail_after=2, recover_after=2)
    settings = (Setting("url", "URL"),)

    def __init__(self):
        self.ok = True

    def sync(self, ctx):
        return [Sample(key="https://site", ok=self.ok, summary="200" if self.ok else "500", values={}, url="https://site")]


def sync(core, sid):
    asyncio.run(core.sync_now(sid))


@pytest.fixture
def items_core():
    plugin = ItemsPlugin()
    core = Core({"items": plugin}, Store(":memory:"))
    sid = core.add_source("items", "Work", {"account": "me"})
    return core, plugin, sid


def visible(core):
    return [v.item.id for v in core.get_state().sources[0].items]


def test_first_sync_is_silent_then_new_items_notify(items_core):
    core, plugin, sid = items_core
    plugin.next = [item("1"), item("2")]
    sync(core, sid)
    assert core.events_since(0) == ()

    plugin.next = [item("1"), item("2"), item("3")]
    sync(core, sid)
    [event] = core.events_since(0)
    assert (event.kind, event.title, event.body, event.url) == (NEW_ITEM, "Work: review requested", "PR 3", "https://x/3")
    assert core._desktop.sent == [[event]]
    assert core.events_since(event.seq) == ()


def test_ignore_hides_and_survives_syncs(items_core):
    core, plugin, sid = items_core
    plugin.next = [item("1"), item("2")]
    sync(core, sid)
    core.ignore_item(sid, "1")
    assert visible(core) == ["2"]
    sync(core, sid)
    assert visible(core) == ["2"] and core.get_state().sources[0].ignored == 1


def test_ignored_item_reappears_and_notifies_after_leaving(items_core):
    core, plugin, sid = items_core
    plugin.next = [item("1")]
    sync(core, sid)
    core.ignore_item(sid, "1")
    plugin.next = []  # you reviewed it: GitHub drops the request
    sync(core, sid)
    plugin.next = [item("1")]  # author re-requests
    sync(core, sid)
    assert visible(core) == ["1"]
    assert [e.body for e in core.events_since(0)] == ["PR 1"]


@pytest.fixture
def probe_core():
    plugin = ProbePlugin()
    core = Core({"probe": plugin}, Store(":memory:"))
    sid = core.add_source("probe", "Site", {"url": "https://site"})
    return core, plugin, sid


def run_checks(core, plugin, sid, results):
    for ok in results:
        plugin.ok = ok
        sync(core, sid)


def health(core):
    return core.get_state().sources[0].health[0]


def test_failing_notifies_once_per_outage(probe_core):
    core, plugin, sid = probe_core
    run_checks(core, plugin, sid, [False, False, False, False])
    events = core.events_since(0)
    assert [e.kind for e in events] == [FAILING]
    assert events[0].title == "✗ Site is failing"

    run_checks(core, plugin, sid, [True, True, False, False])  # recover, then a new outage
    assert [e.kind for e in core.events_since(0)] == [FAILING, FAILING]


def test_acknowledge_and_close_rules(probe_core):
    core, plugin, sid = probe_core
    with pytest.raises(ValueError, match="only a failing row"):
        run_checks(core, plugin, sid, [True])
        core.acknowledge(sid, "https://site")

    run_checks(core, plugin, sid, [False, False])
    core.acknowledge(sid, "https://site")
    assert health(core).acknowledged and health(core).state == "failing"
    with pytest.raises(ValueError, match="still failing"):
        core.close(sid, "https://site")

    run_checks(core, plugin, sid, [True, True])
    assert health(core).state == "ok" and health(core).acknowledged  # orange until closed
    core.close(sid, "https://site")
    assert not health(core).acknowledged
    with pytest.raises(ValueError, match="nothing to close"):
        core.close(sid, "https://site")


def test_new_outage_clears_old_acknowledgment(probe_core):
    core, plugin, sid = probe_core
    run_checks(core, plugin, sid, [False, False])
    core.acknowledge(sid, "https://site")
    run_checks(core, plugin, sid, [True, True, False, False])  # recovered but not closed, fails again
    assert health(core).state == "failing" and not health(core).acknowledged


def event(kind=NEW_ITEM, body="PR 1"):
    return Event(1, 0.0, kind, "s", "Work", f"Work: {body}", body)


def test_desktop_channel_single_and_batched():
    calls = []
    channel = DesktopChannel(run=lambda args, **kw: calls.append(args), which=lambda _: "/usr/bin/notify-send")
    channel.send([event()])
    channel.send([event(body="A"), event(body="B"), event(FAILING, body="C")])
    assert calls[0][-2:] == ["Work: PR 1", "PR 1"] and "normal" in calls[0]
    assert calls[1][-2] == "3 new on Work" and "critical" in calls[1]
    assert calls[1][-1] == "• A\n• B\n• C"


def test_desktop_channel_without_notify_send_or_daemon():
    calls = []
    DesktopChannel(run=lambda *a, **k: calls.append(a), which=lambda _: None).send([event()])
    assert calls == []

    def boom(*a, **k):
        raise OSError("no dbus")

    DesktopChannel(run=boom, which=lambda _: "/usr/bin/notify-send").send([event()])  # must not raise
