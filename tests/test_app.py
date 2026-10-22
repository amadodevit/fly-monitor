import asyncio

from textual.widgets import DataTable

from fly_monitor.core.engine import Core
from fly_monitor.core.models import Item
from fly_monitor.core.plugin import Setting
from fly_monitor.core.store import Store
from fly_monitor.ui.app import FlyMonitorApp
from fly_monitor.ui.layout import depth_name


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
            table.move_cursor(row=table.get_row_index(f"{sid}\x1f2"))  # newest item
            await pilot.press("i")
            await pilot.pause(1.2)
            titles = [str(table.get_row_at(r)[1]) for r in range(table.row_count)]
            header_status = str(table.get_row(f"hdr:{sid}")[1])
            return titles, header_status

    titles, header_status = asyncio.run(run())
    assert "PR 2" not in titles and "PR 1" in titles  # newest (updated_at "2") was first, now hidden
    assert "1 ignored" in header_status




def test_g_cycles_grouping_and_remembers_it():
    core = Core({"items": ItemsPlugin()}, Store(":memory:"))
    core.add_source("items", "Work", {"account": "me"}, client="Acme")

    async def run():
        app = FlyMonitorApp(core)
        app.core.run = lambda: asyncio.sleep(3600)
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.pause(1.2)
            first = str(app.query_one(DataTable).get_row_at(0)[0]).strip()
            await pilot.press("g")
            await pilot.pause(0.2)
            second = str(app.query_one(DataTable).get_row_at(0)[0]).strip()
            return first, second

    assert asyncio.run(run()) == ("▾ Acme", "▾ items")
    assert core.preference("grouping") == "Kind"


def test_plus_and_minus_step_through_levels():
    core = Core({"items": ItemsPlugin()}, Store(":memory:"))
    for client in ("Acme", "Globex"):
        sid = core.add_source("items", client, {"account": client}, client=client)
        asyncio.run(core.sync_now(sid))

    async def run():
        app = FlyMonitorApp(core)
        app.core.run = lambda: asyncio.sleep(3600)
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.pause(1.2)
            table = app.query_one(DataTable)
            counts = [table.row_count]
            for key in ("minus", "minus", "minus", "plus", "plus", "plus", "plus"):
                await pilot.press(key)
                await pilot.pause(0.1)
                counts.append(table.row_count)
            return counts

    # per client: group, kind, source, 2 items. Level 2 (start) shows all 10; items sources are open by default.
    # -: kinds folded -> client+kind = 4; -: clients only = 2; - at the top stays 2; then back up.
    assert asyncio.run(run()) == [10, 4, 2, 2, 4, 10, 10, 10]


def test_level_names():
    levels = ("client", "kind")
    assert [depth_name(levels, d) for d in range(4)] == [
        "Showing clients",
        "Showing clients and kinds",
        "Showing clients, kinds and sources",
        "Showing everything",
    ]
