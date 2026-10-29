"""Regenerate the README screenshots from made-up data.

The real plugins are subclassed so their settings, metrics and checks are what users see, but `sync`
returns scripted samples and items instead of reaching a network. Run from the repo root:

    uv run python scripts/screenshots.py

Writes SVGs to docs/screenshots/. The README uses PNGs of them, rendered in a browser so the
terminal font loads (GitHub blocks fonts inside SVG images). Every name here is invented.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from watchkeep.core import engine
from watchkeep.core.engine import Core
from watchkeep.core.models import Item, Sample
from watchkeep.core.panels import Panel, PanelFilter
from watchkeep.core.store import Store
from watchkeep.plugins.activemq import ActiveMQSource
from watchkeep.plugins.feeds import FeedsSource
from watchkeep.plugins.github import GitHubSource
from watchkeep.plugins.website import WebsiteSource
from watchkeep.ui.app import WatchkeepApp

OUT = Path("docs/screenshots")
SIZE = (180, 32)
NOW = datetime.now(timezone.utc)


def ago(**delta) -> str:
    return (NOW - timedelta(**delta)).isoformat()


class Silent:
    def send(self, events):
        pass


class Broker(ActiveMQSource):
    syncs = 0

    def sync(self, ctx):
        Broker.syncs += 1
        queues = {
            "orders.created": (0, 4, 1200, 1200),
            "orders.shipped": (3, 2, 410, 407),
            "billing.invoices": (0, 1, 96, 96),
            "billing.refunds": (0, 1, 12, 12),
            "notifications.email": (14, 3, 2300, 2286),
            "search.reindex": (0, 1, 40, 40),
            "orders.payments": (37 + 9 * Broker.syncs, 0, 300 + 9 * Broker.syncs, 300),  # consumer died
        }
        return [
            Sample(
                key=name,
                ok=True,
                summary=f"{size} waiting · {consumers} consumers",
                values={"size": size, "consumers": consumers, "enqueued": enq * Broker.syncs, "dequeued": deq * Broker.syncs},
            )
            for name, (size, consumers, enq, deq) in queues.items()
        ]


class Sites(WebsiteSource):
    def sync(self, ctx):
        url = ctx.settings["url"]
        ms = {"https://shop.example.com": 184, "https://api.example.com/health": 92}.get(url, 311)
        return [Sample(key=url, ok=True, summary=f"200 · {ms} ms", values={"status": 200, "response_ms": ms}, url=url)]


class GitHub(GitHubSource):
    syncs = 0

    def sync(self, ctx):
        GitHub.syncs += 1
        items = [
            Item("1", "review requested", "Retry failed payment webhooks", "https://github.com/", "acme/checkout", "dana", ago(hours=3)),
            Item("2", "mentioned", "Queue consumer leaks connections on restart", "https://github.com/", "acme/platform", "sam", ago(hours=20)),
            Item("3", "commented", "Add dark mode to the status page", "https://github.com/", "globex/status", "ines", ago(days=2)),
        ]
        if GitHub.syncs > 1:
            items.append(Item("4", "review requested", "Bump the invoice PDF renderer", "https://github.com/", "globex/billing", "omar", ago(minutes=4)))
        return items


class Feeds(FeedsSource):
    def sync(self, ctx):
        return [
            Item("a", "Postgres internals, which you follow", "How VACUUM decides what to skip", "https://example.com/a", "dbweekly.example", "M. Rossi", ago(hours=5)),
            Item("b", "Python packaging", "Entry points without the magic", "https://example.com/b", "pyblog.example", "J. Okafor", ago(days=1)),
        ]


def build() -> Core:
    os.environ.setdefault("FAKE_SECRET", "x")
    engine.DesktopChannel = Silent  # no real popups while taking screenshots
    Broker.syncs = GitHub.syncs = 0
    plugins = {"activemq": Broker(), "website": Sites(), "github": GitHub(), "feeds": Feeds()}
    core = Core(plugins, Store(":memory:"))
    broker = core.add_source("activemq", "mq.acme.example", {"url": "http://mq.acme.example:8161", "password": "env:FAKE_SECRET"}, client="Acme", environment="prod")
    shop = core.add_source("website", "shop.example.com", {"url": "https://shop.example.com"}, client="Acme", environment="prod")
    api = core.add_source("website", "api.example.com", {"url": "https://api.example.com/health"}, client="Globex", environment="prod")
    stage = core.add_source("website", "staging.example.com", {"url": "https://staging.example.com"}, client="Globex", environment="staging")
    gh = core.add_source("github", "octocat", {"account": "octocat", "token": "env:FAKE_SECRET", "watch": "review_requests,comments,mentions"}, client="Acme")
    feeds = core.add_source("feeds", "Feeds", {"feeds": "https://example.com/feed", "interests": "databases, python"}, tags=("personal",))
    core.save_panels([Panel("acme", "acme", PanelFilter(clients=("Acme",))), Panel("globex", "globex", PanelFilter(clients=("Globex",))), Panel("personal", "personal", PanelFilter(tags=("personal",)))])
    for _ in range(3):  # three bad syncs in a row turn orders.payments failing
        asyncio.run(core.sync_now(broker))
    for sid in (shop, api, stage, feeds, gh, gh):
        asyncio.run(core.sync_now(sid))
    return core


async def shoot(core: Core, name: str, keys: tuple[str, ...] = (), wait: float = 1.5) -> None:
    app = WatchkeepApp(core)
    app.core.run = lambda: asyncio.sleep(3600)  # the data above is all there is
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause(wait)
        for key in keys:
            await pilot.press(key)
            await pilot.pause(0.4)
        app.save_screenshot(f"{name}.svg", path=str(OUT))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    asyncio.run(shoot(build(), "overview"))
    asyncio.run(shoot(build(), "panel", ("2", "down", "down", "enter")))  # the acme panel, broker unfolded
    asyncio.run(shoot(build(), "add-source", ("a", "enter")))  # the ActiveMQ form, checks included
    asyncio.run(shoot(build(), "notifications", ("shift+tab",)))
    print(f"wrote {OUT}/*.svg")


if __name__ == "__main__":
    main()
