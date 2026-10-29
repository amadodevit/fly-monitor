"""Panels: named views with a saved filter.

A panel is a filter, not a fixed list, so a source added later shows up in
the right panels without editing them. The filter has match rules on fields
every `SourceView` already has (client, environment, tags, plugin) plus
hand-picked source ids:

- values inside one field are OR ("client is acme or globex");
- different fields are AND ("client is acme and environment is prod");
- a field with no values sets no constraint;
- hand-picked sources are always in, whatever the rules say;
- a filter with no rules and no picks matches nothing.

Values compare trimmed and case-insensitively, the way the forms trim client
and environment. The built-in "All" panel matches every source.

Everything here is pure and works on plain data, so the UI and a future local
HTTP server apply the same filter. No Store, no Textual. Panels are stored as
JSON with a `version` field; unknown keys are ignored so later releases can
add rule kinds without breaking saved panels, and unreadable input falls back
to the default panels instead of stopping the app.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .models import SourceView

VERSION = 1
ALL_ID = "all"

_RULE_FIELDS = ("clients", "environments", "tags", "plugins")
_FIELDS = (*_RULE_FIELDS, "picks")


@dataclass(frozen=True)
class PanelFilter:
    """Match rules plus hand-picked source ids. Empty tuples mean "not set"."""

    clients: tuple[str, ...] = ()
    environments: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    plugins: tuple[str, ...] = ()  # source types
    picks: tuple[str, ...] = ()  # source ids, always included


@dataclass(frozen=True)
class Panel:
    id: str
    name: str
    filter: PanelFilter = PanelFilter()


def _norm(value: str) -> str:
    return value.strip().casefold()


def _wanted(values: tuple[str, ...]) -> set[str]:
    # Blank values are ignored, so "  " never means "client is empty".
    return {n for n in (_norm(v) for v in values) if n}


def matches(f: PanelFilter, source: SourceView) -> bool:
    """Whether `source` belongs in a panel with filter `f`."""
    if source.id in f.picks:
        return True
    rules = [
        (_wanted(f.clients), {_norm(source.client)}),
        (_wanted(f.environments), {_norm(source.environment)}),
        (_wanted(f.tags), {_norm(t) for t in source.tags}),
        (_wanted(f.plugins), {_norm(source.plugin)}),
    ]
    active = [(wanted, have) for wanted, have in rules if wanted]
    if not active:
        return False
    return all(wanted & have for wanted, have in active)


def all_panel() -> Panel:
    """The built-in panel that shows every source."""
    return Panel(id=ALL_ID, name="All")


def is_all(panel: Panel) -> bool:
    return panel.id == ALL_ID


def panel_matches(panel: Panel, source: SourceView) -> bool:
    """Like `matches`, but "All" matches every source whatever its filter."""
    return is_all(panel) or matches(panel.filter, source)


def default_panels() -> list[Panel]:
    """What a first run, or an unreadable saved value, starts with."""
    return [all_panel()]


def to_json(panels: list[Panel]) -> str:
    return json.dumps(
        {
            "version": VERSION,
            "panels": [
                {
                    "id": p.id,
                    "name": p.name,
                    "filter": {k: list(getattr(p.filter, k)) for k in _FIELDS},
                }
                for p in panels
            ],
        }
    )


def _strings(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return None
    return tuple(value)


def _panel_from_dict(data: object) -> Panel | None:
    """One stored panel, or None if it is malformed. Unknown keys are ignored."""
    if not isinstance(data, dict):
        return None
    pid, name, raw = data.get("id"), data.get("name"), data.get("filter", {})
    if not isinstance(pid, str) or not pid or not isinstance(name, str):
        return None
    if not isinstance(raw, dict):
        return None
    fields: dict[str, tuple[str, ...]] = {}
    for key in _FIELDS:
        if key in raw:
            values = _strings(raw[key])
            if values is None:
                return None
            fields[key] = values
    return Panel(id=pid, name=name, filter=PanelFilter(**fields))


def from_json(text: str | None) -> list[Panel]:
    """Read stored panels; anything unreadable gives `default_panels()`.

    Malformed entries and repeated ids are skipped, the rest are kept. "All"
    is always present and first. A newer `version` is read best-effort.
    """
    try:
        data = json.loads(text) if text else None
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("panels"), list):
        return default_panels()
    panels: list[Panel] = []
    seen: set[str] = set()
    for entry in data["panels"]:
        panel = _panel_from_dict(entry)
        if panel is None or panel.id in seen:
            continue
        seen.add(panel.id)
        panels.append(panel)
    if not panels:
        return default_panels()
    alls = [p for p in panels if is_all(p)] or [all_panel()]
    return [alls[0], *(p for p in panels if not is_all(p))]
