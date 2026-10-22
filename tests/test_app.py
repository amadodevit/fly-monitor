import asyncio
import time

from textual.widgets import DataTable

from fly_monitor.core.engine import Core
from fly_monitor.core.models import HealthView, Item, SourceView
from fly_monitor.core.plugin import Setting
from fly_monitor.core.store import Store
from fly_monitor.ui.app import FlyMonitorApp, _attention, _attention_order, _status


class ItemsPlugin:
    name = "items"
    connection = "http"
    settings = (Setting("account", "Account"),)

    def sync(self, ctx):
        return [
            Item(id=i, action="mentioned", title=f"PR {i}", url=f"https://x/{i}", repo="a/b", author="z", updated_at=i)
            for i in ("1", "2")
        ]


def test_pressing_i_hides_the_selected_item():
    core = Core({"items": ItemsPlugin()}, Store(":memory:"))
    sid = core.add_source("items", "Work", {"account": "me"})
    asyncio.run(core.sync_now(sid))

    async def run():
        app = FlyMonitorApp(core)
        app.core.run = lambda: asyncio.sleep(3600)  # no background syncing in this test
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(1.2)
            table = app.query_one(DataTable)
            table.move_cursor(row=1)  # first item under the header
            await pilot.press("i")
            await pilot.pause(1.2)
            titles = [str(table.get_row_at(r)[1]) for r in range(table.row_count)]
            header_status = str(table.get_row_at(0)[1])
            return titles, header_status

    titles, header_status = asyncio.run(run())
    assert "PR 2" not in titles and "PR 1" in titles  # newest (updated_at "2") was first, now hidden
    assert "1 ignored" in header_status


def health_view(key, state="ok", acknowledged=False):
    return HealthView(key=key, state=state, since=0, summary="", failures=0, window=3, checked_at=0, url="", acknowledged=acknowledged)


def source_view(*health, error=None):
    return SourceView(
        id="b", plugin="activemq", name="Broker", tags=(), interval=60, last_sync=time.time(),
        last_error=error, syncing=False, items=(), health=health,
    )


def test_header_counts_and_colour_show_trouble_even_when_folded():
    broker = source_view(health_view("a"), health_view("b", "failing"), health_view("c", "failing", acknowledged=True))
    assert _status(broker).plain == "1 failing · 1 acknowledged · 1 ok · checked just now"
    assert _attention(broker) == "red"
    assert _attention(source_view(health_view("a"), health_view("c", "ok", acknowledged=True))) == "dark_orange"
    assert _attention(source_view(health_view("a"))) == ""
    assert _attention(source_view(error="timeout")) == "red"


def test_failing_rows_come_first():
    rows = [health_view("a"), health_view("b", "ok", acknowledged=True), health_view("c", "failing"), health_view("d", "failing")]
    assert [h.key for h in sorted(rows, key=_attention_order)] == ["c", "d", "b", "a"]
