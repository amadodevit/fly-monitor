"""What the overview shows, as plain rows. Pure: no Textual, so it's easy to test.

    Client › Kind (default)        ▾ Acme                1 failing · 14 ok
                                     ▾ ActiveMQ queues       waiting  consumers  in    out
                                       ▸ Prod broker   1 failing · 5 ok   101  22  +199  +329
                                         ✗ orders      ...
                                         +5 ok

Healthy sources take one line; trouble shows without unfolding anything.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime

from rich.text import Text

from fly_monitor.core.checks import compact
from fly_monitor.core.engine import PluginInfo
from fly_monitor.core.health import FAILING
from fly_monitor.core.models import HealthView, SourceView, State

# (name shown on g, grouping levels outer to inner)
PRESETS = (
    ("Client › Kind", ("client", "kind")),
    ("Kind", ("kind",)),
    ("Environment › Kind", ("environment", "kind")),
    ("Flat", ()),
)
VALUE_COLUMNS = 4
ITEM_COLUMNS = ("repo", "by", "updated")
WIDTHS = {"name": 26, "value": (20, 10, 8, 8), "note": 32}
UNSET = {"client": "No client", "environment": "No environment"}


@dataclass
class Row:
    key: str
    cells: list  # name, title, VALUE_COLUMNS values, note
    kind: str  # group | source | health | item | more
    source_id: str = ""
    ref: str = ""  # health key or item id
    url: str = ""


@dataclass
class View:
    """What the user folded or unfolded. UI-only, not persisted."""

    levels: tuple[str, ...] = PRESETS[0][1]
    expanded: dict[str, bool] = field(default_factory=dict)  # source id -> override of the default fold
    folded_groups: set[str] = field(default_factory=set)
    title_width: int = 50
    depth: int = 2  # what +/- last set: grouping levels shown, then sources, then everything


LEVEL_NAMES = {"client": "clients", "kind": "kinds", "environment": "environments"}


def depth_name(levels: tuple[str, ...], depth: int) -> str:
    """ "Showing clients and kinds" for the toast after +/-."""
    shown = [LEVEL_NAMES[level] for level in levels[: depth + 1]] if depth < len(levels) else [LEVEL_NAMES[level] for level in levels]
    if depth == len(levels):
        shown.append("sources")
    if depth > len(levels):
        return "Showing everything"
    return "Showing " + (" and ".join([", ".join(shown[:-1]), shown[-1]]) if len(shown) > 1 else shown[0])


def build(state: State, plugins: dict[str, PluginInfo], view: View, now: Callable[[], float] = time.time) -> list[Row]:
    rows: list[Row] = []
    ctx = _Ctx(plugins, view, now)
    _group(rows, list(state.sources), view.levels, (), ctx)
    return rows


@dataclass
class _Ctx:
    plugins: dict[str, PluginInfo]
    view: View
    now: Callable[[], float]

    def title(self, source: SourceView) -> str:
        info = self.plugins.get(source.plugin)
        return info.title if info else source.plugin

    def columns(self, source: SourceView) -> tuple[str, ...]:
        info = self.plugins.get(source.plugin)
        if info is None or info.kind != "samples":
            return ITEM_COLUMNS
        return tuple((m.short or m.label.lower()) for m in info.metrics[:VALUE_COLUMNS])


def _group(rows: list[Row], sources: list[SourceView], levels: tuple[str, ...], path: tuple[str, ...], ctx: _Ctx) -> None:
    if not levels:
        for source in sources:
            _source(rows, source, len(path), ctx)
        return
    level, rest = levels[0], levels[1:]
    groups: dict[str, list[SourceView]] = {}
    for source in sources:
        groups.setdefault(_value(source, level, ctx), []).append(source)
    for name in sorted(groups, key=lambda n: (n in UNSET.values(), n.lower())):
        members = groups[name]
        key = "grp:" + "\x1f".join((*path, name))
        folded = key in ctx.view.folded_groups
        indent = "  " * len(path)
        arrow = "▸" if folded else "▾"
        colour = _colour(members)
        if level == "kind":
            labels = ctx.columns(members[0])
            cells = [Text(f"{indent}{arrow} {name}", style=f"bold {colour}".strip()), Text("")]
            cells += _pad(list(labels))  # column names for this kind
            cells.append(Text(""))
        else:
            cells = [Text(f"{indent}{arrow} {name} ", style=f"bold reverse {colour}".strip()), _counts(members, ctx)]
            cells += [Text("")] * (VALUE_COLUMNS + 1)
        rows.append(Row(key, cells, "group"))
        if not folded:
            _group(rows, members, rest, (*path, name), ctx)


def _value(source: SourceView, level: str, ctx: _Ctx) -> str:
    if level == "kind":
        return ctx.title(source)
    return getattr(source, level) or UNSET[level]


def _source(rows: list[Row], source: SourceView, depth: int, ctx: _Ctx) -> None:
    indent = "  " * depth
    note = _source_note(source, ctx)
    key = f"hdr:{source.id}"

    if len(source.health) == 1 and not source.items:  # e.g. a website: everything on one line
        health = source.health[0]
        cells = [Text(f"{indent}  {source.name}", style="bold"), Text(_cut(health.key, ctx.view.title_width), style="dim")]
        cells += _health_values(health, source, ctx)
        cells.append(_health_note(health, ctx) or note)
        rows.append(Row(key, cells, "source", source.id, health.key, health.url))
        return

    expandable = bool(source.items or len(source.health) > 1)
    expanded = ctx.view.expanded.get(source.id, not source.health)  # items open, health folded
    arrow = ("▾ " if expanded else "▸ ") if expandable else "  "
    name = Text(f"{indent}{arrow}{source.name}", style="bold")
    if source.health:
        cells = [name, _health_counts(source), *_aggregate(source, ctx), note]
    else:
        cells = [name, _item_counts(source), *[Text("")] * VALUE_COLUMNS, note]
    rows.append(Row(key, cells, "source", source.id))

    inner = "  " * (depth + 2)
    if source.health:
        ordered = sorted(source.health, key=attention_order)
        shown = ordered if expanded else [h for h in ordered if _needs_you(h)]
        for health in shown:
            cells = [_marker(health, inner), Text(_cut(health.key, ctx.view.title_width))]
            cells += _health_values(health, source, ctx)
            cells.append(_health_note(health, ctx))
            rows.append(Row(f"{source.id}\x1f{health.key}", cells, "health", source.id, health.key, health.url))
        hidden = len(ordered) - len(shown)
        if hidden and shown:
            cells = [Text(f"{inner}+{hidden} ok", style="dim"), Text("enter to show", style="dim")]
            rows.append(Row(f"more:{source.id}", cells + [Text("")] * (VALUE_COLUMNS + 1), "more", source.id))
    elif expanded:
        for view in source.items:
            item = view.item
            label = Text(inner)
            label.append("● " if view.is_new else "  ", style="bold yellow")
            label.append(item.action, style="dim")
            values = [Text(_cut(item.repo, WIDTHS["value"][0])), Text(_cut(item.author, WIDTHS["value"][1])), Text(_ago(_iso(item.updated_at), ctx))]
            cells = [label, Text(_cut(item.title, ctx.view.title_width)), *_pad(values), Text("")]
            rows.append(Row(f"{source.id}\x1f{item.id}", cells, "item", source.id, item.id, item.url))


# -- cells ---------------------------------------------------------------------


def _health_values(health: HealthView, source: SourceView, ctx: _Ctx) -> list[Text]:
    info = ctx.plugins.get(source.plugin)
    metrics = info.metrics[:VALUE_COLUMNS] if info else ()
    if not metrics:
        return _pad([Text(_cut(health.summary, WIDTHS["value"][0]))])
    cells = []
    for metric in metrics:
        if metric.counter:
            grown = health.growth.get(metric.key)
            cells.append(Text("" if grown is None else f"+{compact(grown)}"))
        else:
            value = health.values.get(metric.key)
            cells.append(Text("—" if value is None else compact(value)))
    return _pad(cells)


def _aggregate(source: SourceView, ctx: _Ctx) -> list[Text]:
    """Totals across a source's rows: summed numbers and growth."""
    info = ctx.plugins.get(source.plugin)
    metrics = info.metrics[:VALUE_COLUMNS] if info else ()
    cells = []
    for metric in metrics:
        if metric.counter:
            grown = [h.growth[metric.key] for h in source.health if metric.key in h.growth]
            cells.append(Text(f"+{compact(sum(grown))}" if grown else "", style="dim"))
        else:
            values = [h.values.get(metric.key) for h in source.health]
            numbers = [v for v in values if isinstance(v, int | float)]
            cells.append(Text(compact(sum(numbers)) if numbers and len(source.health) > 1 else "", style="dim"))
    return _pad(cells)


def _marker(health: HealthView, indent: str) -> Text:
    text = Text(indent)
    if health.acknowledged:
        text.append("◐", style="bold dark_orange")
    elif health.state == FAILING:
        text.append("✗", style="bold red")
    elif health.fired:
        text.append("•", style="yellow")  # a bad check, not failing (yet)
    return text


def _health_note(health: HealthView, ctx: _Ctx) -> Text:
    fired = ", ".join(health.fired)
    if health.acknowledged:
        status = "acknowledged" if health.state == FAILING else "recovered · c to close"
        return Text(_cut(" · ".join(p for p in (status, fired) if p), WIDTHS["note"]), style="dark_orange")
    if health.state == FAILING:
        age = _ago(health.since, ctx).removesuffix(" ago").replace("just now", "<1m")
        return Text(_cut(" · ".join(p for p in (f"failing {age}", fired) if p), WIDTHS["note"]), style="red")
    if fired:
        return Text(_cut(fired, WIDTHS["note"]), style="yellow")
    return Text("")


def _source_note(source: SourceView, ctx: _Ctx) -> Text:
    if source.syncing:
        return Text("syncing…", style="italic dim")
    if source.last_error:
        return Text(_cut(f"error: {source.last_error}", WIDTHS["note"]), style="bold red")
    if source.last_sync is None:
        return Text("never synced", style="dim")
    verb = "checked" if source.health else "synced"
    return Text(f"{verb} {_ago(source.last_sync, ctx)}", style="dim")


def _health_counts(source: SourceView) -> Text:
    failing = sum(h.state == FAILING and not h.acknowledged for h in source.health)
    acknowledged = sum(h.acknowledged for h in source.health)
    ok = len(source.health) - failing - acknowledged
    return _join([(failing, "failing", "bold red"), (acknowledged, "acknowledged", "bold dark_orange"), (ok, "ok", "dim")])


def _item_counts(source: SourceView) -> Text:
    count = len(source.items)
    text = Text(f"{count} to look at" if count else "nothing needs you", style="" if count else "dim")
    if source.ignored:
        text.append(f" · {source.ignored} ignored", style="dim")
    return text


def _counts(sources: Iterable[SourceView], ctx: _Ctx) -> Text:
    sources = list(sources)
    health = [h for s in sources for h in s.health]
    failing = sum(h.state == FAILING and not h.acknowledged for h in health)
    acknowledged = sum(h.acknowledged for h in health)
    errors = sum(bool(s.last_error) for s in sources)
    items = sum(len(s.items) for s in sources)
    return _join([
        (errors, "with errors", "bold red"),
        (failing, "failing", "bold red"),
        (acknowledged, "acknowledged", "bold dark_orange"),
        (items, "to look at", ""),
        (len(health) - failing - acknowledged, "ok", "dim"),
    ])


def _join(parts: list[tuple[int, str, str]]) -> Text:
    text = Text()
    for count, label, style in parts:
        if count:
            if text:
                text.append(" · ", style="dim")
            text.append(f"{count} {label}", style=style)
    return text


def _colour(sources: Iterable[SourceView]) -> str:
    """Red needs you, orange is in hand, nothing is fine."""
    sources = list(sources)
    if any(s.last_error for s in sources) or any(h.state == FAILING and not h.acknowledged for s in sources for h in s.health):
        return "red"
    if any(h.acknowledged for s in sources for h in s.health):
        return "dark_orange"
    return ""


def attention_order(health: HealthView) -> tuple[int, str]:
    """Failing first, then acknowledged or recovered-but-open, then bad checks, then OK."""
    if health.state == FAILING and not health.acknowledged:
        return 0, health.key
    if health.acknowledged:
        return 1, health.key
    return (2 if health.fired else 3), health.key


def _needs_you(health: HealthView) -> bool:
    return health.state == FAILING or health.acknowledged


def _pad(cells: list) -> list:
    cells = [c if isinstance(c, Text) else Text(c, style="dim italic") for c in cells]
    return (cells + [Text("")] * VALUE_COLUMNS)[:VALUE_COLUMNS]


def _cut(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _iso(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _ago(ts: float | None, ctx: _Ctx) -> str:
    if ts is None:
        return ""
    seconds = max(0, int(ctx.now() - ts))
    if seconds < 60:
        return "just now"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit} ago"
    return "just now"
