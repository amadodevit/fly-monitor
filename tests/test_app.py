import asyncio

from textual.widgets import DataTable

from fly_monitor.core.engine import Core
from fly_monitor.core.models import Item
from fly_monitor.core.plugin import Setting
from fly_monitor.core.store import Store
from fly_monitor.ui.app import FlyMonitorApp


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
