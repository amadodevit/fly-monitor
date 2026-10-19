"""The core. The UI talks to it only through the public methods below.

Runs in-process for now. The interface is kept small and takes/returns plain
data so it can later sit behind a local HTTP server without changing plugins
or storage.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import DEFAULT_INTERVAL, SourceConfig
from .connections import CONNECTIONS, resolve_secret
from .diff import diff_snapshot
from .health import FAILING, Health, Rule, step
from .models import HealthView, Item, ItemView, Sample, SourceView, State
from .notify import FAILING as FAILING_EVENT
from .notify import NEW_ITEM, DesktopChannel, Event
from .plugin import Setting, SourcePlugin, SyncContext
from .store import Store

ERROR_RETRY = 300  # after a failed sync, try again within 5 min even if the interval is longer
CONFIG_IMPORTED = "config_imported"


@dataclass(frozen=True)
class PluginInfo:
    name: str
    connection: str
    settings: tuple[Setting, ...]
    can_test: bool
    can_detect: bool
    kind: str  # "items" (things that come and go) or "samples" (rows that are always there)
    default_interval: int
    default_rule: Rule | None  # only for "samples"


@dataclass(frozen=True)
class DetectedSource:
    name: str
    settings: dict[str, Any]
    description: str
    existing_id: str | None  # set when a source with the same identity is already added


class Core:
    def __init__(
        self,
        plugins: dict[str, SourcePlugin],
        store: Store,
        *,
        clock: Callable[[], float] = time.time,
        tick: float = 5.0,
        desktop: DesktopChannel | None = None,
    ) -> None:
        self._plugins = plugins
        self._desktop = desktop or DesktopChannel()
        self._events: deque[Event] = deque(maxlen=200)
        self._seq = 0
        self._store = store
        self._clock = clock
        self._tick = tick
        self._all: dict[str, SourceConfig] = {}  # every stored source, in display order
        self._sources: dict[str, SourceConfig] = {}  # the runnable ones
        self._invalid: dict[str, str] = {}  # source id -> why it can't run (e.g. plugin uninstalled)
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_attempt: dict[str, float] = {}
        self._version = 0
        self._cached: State | None = None
        for source in store.list_sources():
            self._load(source)

    # -- interface used by the UI ---------------------------------------------

    def get_state(self) -> State:
        if self._cached is None or self._cached.version != self._version:
            self._cached = self._build_state()
        return self._cached

    async def sync_now(self, source_id: str | None = None) -> None:
        ids = [source_id] if source_id else self._runnable()
        await asyncio.gather(*(self._sync_source(i) for i in ids))

    def events_since(self, seq: int) -> tuple[Event, ...]:
        """Notification events newer than `seq` (0 for all kept). Events live in memory only."""
        return tuple(e for e in self._events if e.seq > seq)

    def ignore_item(self, source_id: str, item_id: str) -> None:
        """Hide an item. It stays hidden while the source keeps reporting it; if it leaves and
        comes back (e.g. a review re-requested after you reviewed), it shows up as new."""
        self._store.ignore(source_id, item_id)
        self._bump()

    def acknowledge(self, source_id: str, key: str) -> None:
        health = self._health_of(source_id, key)
        if health.state != FAILING:
            raise ValueError("only a failing row can be acknowledged")
        self._store.set_health(source_id, key, Health(health.state, health.since, health.recent, acknowledged=True))
        self._bump()

    def close(self, source_id: str, key: str) -> None:
        health = self._health_of(source_id, key)
        if not health.acknowledged:
            raise ValueError("nothing to close: this row isn't acknowledged")
        if health.state == FAILING:
            raise ValueError("still failing: close it once it has recovered")
        self._store.set_health(source_id, key, Health(health.state, health.since, health.recent, acknowledged=False))
        self._bump()

    def list_plugins(self) -> tuple[PluginInfo, ...]:
        return tuple(
            PluginInfo(
                name=p.name,
                connection=p.connection,
                settings=tuple(p.settings),
                can_test=hasattr(p, "test_connection"),
                can_detect=hasattr(p, "detect"),
                kind=_kind(p),
                default_interval=getattr(p, "default_interval", DEFAULT_INTERVAL),
                default_rule=getattr(p, "default_rule", Rule()) if _kind(p) == "samples" else None,
            )
            for p in sorted(self._plugins.values(), key=lambda p: p.name)
        )

    def add_source(
        self,
        plugin: str,
        name: str,
        settings: dict[str, Any],
        *,
        interval: int = DEFAULT_INTERVAL,
        tags: tuple[str, ...] = (),
        source_id: str | None = None,
        rule: dict[str, int] | None = None,
    ) -> str:
        """Validate, persist and schedule a new source. Returns its id. Raises ValueError."""
        if not name.strip():
            raise ValueError("name is required")
        settings = self._complete_settings(plugin, settings)
        stored_rule = None
        if _kind(self._plugins[plugin]) == "samples":
            default = getattr(self._plugins[plugin], "default_rule", Rule())
            stored_rule = (Rule.from_dict(rule) if rule else default).to_dict()  # validates
        source = SourceConfig(
            id=source_id or self._new_id(name),
            plugin=plugin,
            name=name.strip(),
            interval=interval,
            tags=tags,
            settings=settings,
            rule=stored_rule,
        )
        if source.id in self._all:
            raise ValueError(f"a source with id {source.id!r} already exists")
        self._store.add_source(source, self._secret_keys(plugin))
        self._load(source)
        self._bump()
        return source.id

    def remove_source(self, source_id: str) -> None:
        self._store.remove_source(source_id)
        self._all.pop(source_id, None)
        self._sources.pop(source_id, None)
        self._invalid.pop(source_id, None)
        self._locks.pop(source_id, None)
        self._last_attempt.pop(source_id, None)
        self._bump()

    def detect_sources(self, plugin: str) -> tuple[DetectedSource, ...]:
        """Blocking. Ask a plugin what it can find on this machine, marking ones already added."""
        instance = self._plugins[plugin]
        if not hasattr(instance, "detect"):
            return ()
        identity = tuple(getattr(instance, "identity", ()))
        existing = {
            tuple(s.settings.get(k) for k in identity): s.id
            for s in self._all.values()
            if s.plugin == plugin
        }
        return tuple(
            DetectedSource(
                name=c.name,
                settings=dict(c.settings),
                description=c.description,
                existing_id=existing.get(tuple(c.settings.get(k) for k in identity)) if identity else None,
            )
            for c in instance.detect()
        )

    def test_settings(self, plugin: str, settings: dict[str, Any]) -> str:
        """Blocking. Ask the plugin to verify these settings work, before saving."""
        settings = self._complete_settings(plugin, settings)
        instance = self._plugins[plugin]
        if not hasattr(instance, "test_connection"):
            return "plugin has no connection test"
        with CONNECTIONS[instance.connection]() as conn:
            return instance.test_connection(SyncContext(settings=self._resolve(plugin, settings), http=conn))

    def test_connection(self, source_id: str) -> str:
        """Blocking. Test an existing source."""
        if source_id in self._invalid:
            raise RuntimeError(self._invalid[source_id])
        source = self._sources[source_id]
        return self.test_settings(source.plugin, source.settings)

    def import_config(self, sources: list[SourceConfig]) -> int:
        """One-time import of config.toml. Does nothing after the first successful run."""
        if self._store.get_meta(CONFIG_IMPORTED) or self._all:
            return 0
        for s in sources:
            self.add_source(s.plugin, s.name, s.settings, interval=s.interval, tags=s.tags, source_id=s.id)
        self._store.set_meta(CONFIG_IMPORTED, str(self._clock()))
        return len(sources)

    def source_ids(self) -> list[str]:
        return list(self._all)

    # -- scheduling -------------------------------------------------------------

    async def run(self) -> None:
        """Sync each source when it is due. Runs until cancelled."""
        while True:
            due = [i for i in self._runnable() if self._is_due(i)]
            if due:
                await asyncio.gather(*(self._sync_source(i) for i in due))
            await asyncio.sleep(self._tick)

    def _runnable(self) -> list[str]:
        return list(self._sources)

    def _is_due(self, source_id: str) -> bool:
        if self._locks[source_id].locked():
            return False
        last_sync, last_error = self._store.source_state(source_id)
        last = self._last_attempt.get(source_id, last_sync)
        if last is None:
            return True
        wait = self._sources[source_id].interval
        if last_error:
            wait = min(wait, ERROR_RETRY)
        return self._clock() - last >= wait

    # -- syncing ----------------------------------------------------------------

    async def _sync_source(self, source_id: str) -> None:
        lock = self._locks.get(source_id)
        if lock is None or lock.locked():
            return None  # removed, or already syncing
        async with lock:
            self._bump()
            self._last_attempt[source_id] = self._clock()
            source = self._sources[source_id]
            try:
                items = await asyncio.to_thread(self._fetch, source)
            except Exception as exc:  # a broken source must not take the core down
                if source_id in self._sources:
                    self._store.record_error(source_id, _describe(exc))
                return None
            else:
                if source_id not in self._sources:
                    return None  # removed while syncing
                if _kind(self._plugins[source.plugin]) == "samples":
                    events = self._apply_samples(source, items)
                else:
                    events = self._apply(source, items)
            finally:
                self._bump()
        if events:
            await asyncio.to_thread(self._desktop.send, events)

    def _fetch(self, source: SourceConfig) -> list[Item] | list[Sample]:
        plugin = self._plugins[source.plugin]
        with CONNECTIONS[plugin.connection]() as conn:
            return plugin.sync(SyncContext(settings=self._resolve(source.plugin, source.settings), http=conn))

    def _apply(self, source: SourceConfig, items: list[Item]) -> list[Event]:
        now = self._clock()
        first_sync = self._store.source_state(source.id)[0] is None
        previous = self._store.snapshot(source.id)
        diff = diff_snapshot({k: item for k, (item, _) in previous.items()}, items)
        kept: dict[str, tuple[Item, float]] = {i.id: (i, now) for i in diff.new}
        for item in diff.updated + diff.unchanged:
            kept[item.id] = (item, previous[item.id][1])
        self._store.replace_snapshot(source.id, kept, now)
        self._store.forget_ignored_except(source.id, set(kept))
        if first_sync:
            return []  # adding a source shouldn't announce everything it already had
        return [
            self._event(NEW_ITEM, source, title=f"{source.name}: {item.action}", body=item.title, url=item.url)
            for item in diff.new
        ]

    def _apply_samples(self, source: SourceConfig, samples: list[Sample]) -> list[Event]:
        now = self._clock()
        rule = Rule.from_dict(source.rule)
        previous = self._store.health(source.id)
        latest: dict[str, Sample] = {}
        for sample in samples:
            latest.setdefault(sample.key, sample)  # one result per row per sync
        health = {key: step(previous.get(key), sample.ok, now, rule) for key, sample in latest.items()}
        self._store.record_samples(source.id, list(latest.values()), health, now)
        return [
            self._event(FAILING_EVENT, source, title=f"✗ {source.name} is failing", body=f"{key}: {latest[key].summary}", url=latest[key].url)
            for key, h in health.items()
            if h.state == FAILING and (previous.get(key) is None or previous[key].state != FAILING)
        ]

    def _event(self, kind: str, source: SourceConfig, *, title: str, body: str, url: str) -> Event:
        self._seq += 1
        event = Event(self._seq, self._clock(), kind, source.id, source.name, title, body, url)
        self._events.append(event)
        return event

    def _health_of(self, source_id: str, key: str) -> Health:
        health = self._store.health(source_id).get(key)
        if health is None:
            raise ValueError("no such row")
        return health

    # -- sources ----------------------------------------------------------------

    def _load(self, source: SourceConfig) -> None:
        self._all[source.id] = source
        plugin = self._plugins.get(source.plugin)
        if plugin is None:
            self._invalid[source.id] = f"plugin {source.plugin!r} is not installed"
        elif plugin.connection not in CONNECTIONS:
            self._invalid[source.id] = f"plugin needs unknown connection {plugin.connection!r}"
        else:
            self._sources[source.id] = source
            self._locks[source.id] = asyncio.Lock()

    def _complete_settings(self, plugin: str, settings: dict[str, Any]) -> dict[str, Any]:
        instance = self._plugins.get(plugin)
        if instance is None:
            raise ValueError(f"no plugin named {plugin!r} is installed")
        result = {k: v for k, v in settings.items() if v not in (None, "", [])}
        for setting in instance.settings:
            if setting.key in result:
                continue
            if setting.choices and setting.key not in settings:  # omitted entirely: use defaults
                result[setting.key] = [c.value for c in setting.choices if c.default]
            elif setting.default is not None:
                result[setting.key] = setting.default
        missing = [s.label for s in instance.settings if s.required and s.key not in result]
        if missing:
            raise ValueError(f"missing: {', '.join(missing)}")
        return result

    def _secret_keys(self, plugin: str) -> set[str]:
        return {s.key for s in self._plugins[plugin].settings if s.secret}

    def _resolve(self, plugin: str, settings: dict[str, Any]) -> dict[str, Any]:
        resolved = dict(settings)
        for key in self._secret_keys(plugin) & resolved.keys():
            resolved[key] = resolve_secret(str(resolved[key]))
        return resolved

    def _new_id(self, name: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "source"
        taken = set(self.source_ids())
        candidate, n = base, 2
        while candidate in taken:
            candidate, n = f"{base}-{n}", n + 1
        return candidate

    # -- state ------------------------------------------------------------------

    def _bump(self) -> None:
        self._version += 1

    def _build_state(self) -> State:
        views = []
        for source in self._all.values():
            last_sync, last_error = self._store.source_state(source.id)
            snapshot = self._store.snapshot(source.id)
            ignored = self._store.ignored(source.id)
            items = sorted(
                (
                    ItemView(item=i, first_seen=first, is_new=first == last_sync)
                    for i, first in snapshot.values()
                    if i.id not in ignored
                ),
                key=lambda v: v.item.updated_at,
                reverse=True,
            )
            lock = self._locks.get(source.id)
            rule = Rule.from_dict(source.rule)
            health = self._store.health(source.id)
            health_views = tuple(
                HealthView(
                    key=key,
                    state=health[key].state,
                    since=health[key].since,
                    summary=sample.summary,
                    failures=health[key].failures(rule),
                    window=rule.window,
                    checked_at=taken_at,
                    url=sample.url,
                    acknowledged=health[key].acknowledged,
                )
                for key, (sample, taken_at) in sorted(self._store.latest_samples(source.id).items())
                if key in health
            )
            views.append(
                SourceView(
                    id=source.id,
                    plugin=source.plugin,
                    name=source.name,
                    tags=source.tags,
                    interval=source.interval,
                    last_sync=last_sync,
                    last_error=self._invalid.get(source.id, last_error),
                    syncing=bool(lock and lock.locked()),
                    items=tuple(items),
                    health=health_views,
                    ignored=len(ignored & snapshot.keys()),
                )
            )
        return State(version=self._version, sources=tuple(views))


def _kind(plugin: SourcePlugin) -> str:
    return getattr(plugin, "kind", "items")


def _describe(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    return text.splitlines()[0][:200]
