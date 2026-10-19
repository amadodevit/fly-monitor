import asyncio

from textual.app import App
from textual.widgets import Input, OptionList, SelectionList, Static

from fly_monitor.core.engine import Core
from fly_monitor.core.health import Rule
from fly_monitor.core.plugin import Candidate, Choice, Setting
from fly_monitor.core.store import Store
from fly_monitor.ui.forms import AddSourceScreen


class WebPlugin:
    """No detection, no choices: type -> manual -> watch(more options only)."""

    name = "web"
    connection = "http"
    settings = (
        Setting("url", "URL", help="what to check"),
        Setting("password", "Password", secret=True, required=False),
    )

    def sync(self, ctx):
        return []


class GhLikePlugin:
    """Detects two accounts; has a Watch choice."""

    name = "ghlike"
    connection = "http"
    identity = ("account",)
    settings = (
        Setting("account", "Account"),
        Setting("token", "Token", secret=True),
        Setting("watch", "Watch", choices=(Choice("reviews", "Reviews"), Choice("mentions", "Mentions"))),
    )

    def __init__(self, found=None):
        self.found = found if found is not None else [
            Candidate("work", {"account": "work", "token": "gh:work"}),
            Candidate("home", {"account": "home", "token": "gh:home"}),
        ]

    def detect(self):
        return self.found

    def sync(self, ctx):
        return []


def run_wizard(core, steps):
    """Open the wizard and run `steps(app, pilot)`; return what it dismissed with."""
    result = {}

    class Host(App):
        def on_mount(self):
            self.push_screen(AddSourceScreen(core), lambda ids: result.setdefault("ids", ids))

    async def run():
        app = Host()
        async with app.run_test(size=(100, 50)) as pilot:
            await pilot.pause()
            await steps(app, pilot)
            await pilot.pause()

    asyncio.run(run())
    return result.get("ids")


async def pick_type(app, pilot, name):
    types = app.screen.query_one("#types", OptionList)
    types.highlighted = [o.id for o in types.options].index(name)
    types.focus()
    await pilot.press("enter")
    await pilot.pause()


async def wait_for(app, pilot, selector):
    for _ in range(30):
        await pilot.pause(0.05)
        if app.screen.query(selector):
            return
    raise AssertionError(f"{selector} never appeared")


def result_text(app):
    return str(app.screen.query_one("#result", Static).render())


def test_detected_accounts_skip_typing_and_respect_watch():
    core = Core({"ghlike": GhLikePlugin(), "web": WebPlugin()}, Store(":memory:"))
    core.add_source("ghlike", "work", {"account": "work", "token": "gh:work"})

    async def steps(app, pilot):
        await pick_type(app, pilot, "ghlike")
        await wait_for(app, pilot, "#accounts")
        accounts = app.screen.query_one("#accounts", SelectionList)
        assert accounts.get_option_at_index(0).disabled  # "work" already added
        assert not app.screen.query(Input)  # no username/token fields on the main path
        accounts.select(1)
        await pilot.click("#next-found")
        await wait_for(app, pilot, "#setting-watch")
        app.screen.query_one("#setting-watch", SelectionList).deselect("mentions")
        await pilot.click("#add")

    assert run_wizard(core, steps) == ["home"]
    home = core.get_state().sources[1]
    assert home.name == "home" and home.interval == 3600
    assert core._all["home"].settings == {"account": "home", "token": "gh:home", "watch": ["reviews"]}


def test_several_accounts_in_one_pass():
    core = Core({"ghlike": GhLikePlugin()}, Store(":memory:"))

    async def steps(app, pilot):
        await pick_type(app, pilot, "ghlike")
        await wait_for(app, pilot, "#accounts")
        app.screen.query_one("#accounts", SelectionList).select_all()
        await pilot.click("#next-found")
        await wait_for(app, pilot, "#add")
        assert "Add 2 sources" in str(app.screen.query_one("#add").label)
        await pilot.click("#add")

    assert run_wizard(core, steps) == ["work", "home"]


def test_next_without_ticking_shows_error():
    core = Core({"ghlike": GhLikePlugin()}, Store(":memory:"))

    async def steps(app, pilot):
        await pick_type(app, pilot, "ghlike")
        await wait_for(app, pilot, "#accounts")
        await pilot.click("#next-found")
        assert "Tick at least one" in result_text(app)

    assert run_wizard(core, steps) is None


def test_nothing_found_falls_back_to_manual():
    core = Core({"ghlike": GhLikePlugin(found=[])}, Store(":memory:"))

    async def steps(app, pilot):
        await pick_type(app, pilot, "ghlike")
        await wait_for(app, pilot, "#setting-account")
        assert "Nothing found" in result_text(app)
        assert app.screen.query_one("#setting-token", Input).password
        app.screen.query_one("#setting-account", Input).value = "someone"
        app.screen.query_one("#setting-token", Input).value = "not-a-real-token"
        await pilot.click("#next-manual")
        await wait_for(app, pilot, "#add")
        await pilot.click("#add")

    assert run_wizard(core, steps) == ["someone"]
    assert core._all["someone"].settings["watch"] == ["reviews", "mentions"]


def test_manual_validates_and_more_options_apply():
    core = Core({"web": WebPlugin()}, Store(":memory:"))

    async def steps(app, pilot):
        await pick_type(app, pilot, "web")
        await wait_for(app, pilot, "#setting-url")
        await pilot.click("#next-manual")
        assert "missing: URL" in result_text(app)
        await pilot.pause(0.6)  # let the click chain expire, or the next click counts as a double-click
        app.screen.query_one("#setting-url", Input).value = "https://example.com"
        await pilot.click("#next-manual")
        await wait_for(app, pilot, "#interval")
        app.screen.query_one("#interval", Input).value = "5m"
        app.screen.query_one("#tags", Input).value = "prod, web"
        await pilot.click("#add")

    [sid] = run_wizard(core, steps)
    view = core.get_state().sources[0]
    assert (view.name, view.interval, view.tags) == ("https://example.com", 300, ("prod", "web"))


def test_escape_goes_back_then_cancels():
    core = Core({"ghlike": GhLikePlugin(found=[]), "web": WebPlugin()}, Store(":memory:"))

    async def steps(app, pilot):
        await pick_type(app, pilot, "web")
        await wait_for(app, pilot, "#setting-url")
        await pilot.press("escape")
        await wait_for(app, pilot, "#types")
        await pilot.press("escape")

    assert run_wizard(core, steps) == []
    assert core.get_state().sources == ()


class ProbePlugin:
    name = "probe"
    connection = "http"
    kind = "samples"
    default_interval = 300
    default_rule = Rule(window=10, fail_after=5, recover_after=3)
    settings = (Setting("url", "URL"),)

    def sync(self, ctx):
        return []


def test_sample_plugin_wizard_shows_rule_and_uses_plugin_interval():
    core = Core({"probe": ProbePlugin()}, Store(":memory:"))

    async def steps(app, pilot):
        await pick_type(app, pilot, "probe")
        await wait_for(app, pilot, "#setting-url")
        app.screen.query_one("#setting-url", Input).value = "https://example.com"
        await pilot.click("#next-manual")
        await wait_for(app, pilot, "#rule-fail")
        assert str(app.screen.query_one("#title", Static).render()) == "When is it failing?"
        assert [app.screen.query_one(f"#rule-{k}", Input).value for k in ("fail", "window", "recover")] == ["5", "10", "3"]
        assert app.screen.query_one("#interval", Input).placeholder.startswith("5m")
        app.screen.query_one("#rule-recover", Input).value = "2"
        await pilot.click("#add")

    [sid] = run_wizard(core, steps)
    source = core._all[sid]
    assert source.interval == 300
    assert source.rule == {"window": 10, "fail_after": 5, "recover_after": 2}
