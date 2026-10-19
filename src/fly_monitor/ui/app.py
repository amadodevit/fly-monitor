"""Terminal UI. Reads state from the core; never touches plugins or storage."""

from __future__ import annotations

import shutil
import subprocess
import time
import webbrowser
from datetime import datetime

from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header

from fly_monitor.core.engine import Core
from fly_monitor.core.health import FAILING
from fly_monitor.core.models import HealthView, SourceView, State
from fly_monitor.ui.forms import AddSourceScreen, ConfirmScreen

TITLE_WIDTH = 70


class FlyMonitorApp(App):
    TITLE = "fly-monitor"
    CSS = "DataTable { height: 1fr; }"
    BINDINGS = [
        ("enter", "open", "Open"),
        ("r", "sync", "Sync now"),
        ("a", "add", "Add source"),
        ("d", "delete", "Delete source"),
        ("i", "ignore", "Ignore"),
        ("k", "acknowledge", "Acknowledge"),
        ("c", "close", "Close"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, core: Core, notice: str = "") -> None:
        super().__init__()
        self.core = core
        self._notice = notice
        self._drawn_version = -1
        self._urls: dict[str, str] = {}
        self._rows: dict[str, tuple[str, str, str]] = {}  # row key -> (kind, source id, item id / health key)
        self._last_event = 0
        self._collapsed: set[str] = set()  # source ids; UI-only, not persisted

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Status", "Title", "Detail", "By", "Updated")
        self.run_worker(self.core.run(), group="core")
        self.set_interval(1, self._poll)
        self.set_interval(30, self._redraw)  # keep "3m ago" fresh
        self._poll()
        if self._notice:
            self.notify(self._notice, timeout=10)

    def _poll(self) -> None:
        for event in self.core.events_since(self._last_event):
            self._last_event = event.seq
            failing = event.kind == "failing"
            self.notify(event.body, title=event.title, severity="error" if failing else "information", timeout=8)
        state = self.core.get_state()
        if state.version != self._drawn_version:
            self._draw(state)

    def _redraw(self) -> None:
        self._draw(self.core.get_state())

    def _draw(self, state: State) -> None:
        table = self.query_one(DataTable)
        selected = None
        if table.row_count:
            selected = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

        table.clear()
        self._urls = {}
        self._rows = {}
        if not state.sources:
            table.add_row(Text(" no sources yet ", style="reverse"), Text("press a to add one", style="dim"), "", "", "", key="none")
        for source in state.sources:
            collapsed = source.id in self._collapsed
            arrow = "▸" if collapsed else "▾"
            table.add_row(
                Text(f" {arrow} {source.name} ", style="bold reverse"),
                _status(source),
                "",
                "",
                "",
                key=f"hdr:{source.id}",
            )
            if collapsed:
                continue
            for n, health in enumerate(source.health):
                key = f"{source.id}\x1f{health.key}"
                self._rows[key] = ("health", source.id, health.key)
                if health.url:
                    self._urls[key] = health.url
                table.add_row(*_health_row(health, last=n == len(source.health) - 1), key=key)
            last = len(source.items) - 1
            for n, view in enumerate(source.items):
                item = view.item
                key = f"{source.id}\x1f{item.id}"
                self._urls[key] = item.url
                self._rows[key] = ("item", source.id, item.id)
                label = Text("  └ " if n == last else "  ├ ", style="dim")
                label.append("● " if view.is_new else "  ", style="bold yellow")
                label.append(item.action)
                table.add_row(
                    label,
                    _truncate(item.title),
                    item.repo,
                    item.author,
                    _ago(_parse_iso(item.updated_at)),
                    key=key,
                )
            if not source.items and not source.health and not source.last_error and source.last_sync:
                table.add_row(Text("  └ ", style="dim"), Text("nothing needs you", style="dim"), "", "", "", key=f"empty:{source.id}")

        if selected is not None:
            try:
                table.move_cursor(row=table.get_row_index(selected))
            except Exception:
                pass  # row is gone
        self._drawn_version = state.version

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._open(event.row_key.value)

    def action_open(self) -> None:
        table = self.query_one(DataTable)
        if table.row_count:
            self._open(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)

    def _open(self, key: str | None) -> None:
        if key and key.startswith("hdr:"):
            self._collapsed ^= {key.removeprefix("hdr:")}
            self._redraw()
            return
        url = self._urls.get(key or "")
        if not url:
            return
        if shutil.which("xdg-open"):
            # Keep the browser's output from scribbling over the TUI.
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            webbrowser.open(url)
        self.notify(url, title="Opened")

    def _selected_source(self) -> str | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value or ""
        for prefix in ("hdr:", "empty:"):
            if key.startswith(prefix):
                return key.removeprefix(prefix)
        return key.split("\x1f", 1)[0] if "\x1f" in key else None

    def action_add(self) -> None:
        def added(source_ids: list[str] | None) -> None:
            for source_id in source_ids or []:
                self.run_worker(self.core.sync_now(source_id), group="sync")
            if source_ids:
                self.notify(f"Added {', '.join(source_ids)}, syncing")

        self.push_screen(AddSourceScreen(self.core), added)

    def action_delete(self) -> None:
        source_id = self._selected_source()
        if source_id is None:
            return
        name = next((s.name for s in self.core.get_state().sources if s.id == source_id), source_id)

        def confirmed(yes: bool | None) -> None:
            if yes:
                self.core.remove_source(source_id)
                self.notify(f"Deleted {name}")

        self.push_screen(ConfirmScreen(f"Delete source '{name}' and its history?"), confirmed)

    def _selected_row(self) -> tuple[str, str, str] | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        return self._rows.get(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value or "")

    def _row_action(self, kind: str, action, done: str) -> None:
        row = self._selected_row()
        if row is None or row[0] != kind:
            self.notify(f"Select a {'GitHub item' if kind == 'item' else 'website'} row first", severity="warning")
            return
        try:
            action(row[1], row[2])
        except ValueError as exc:
            self.notify(str(exc), severity="warning")
            return
        self.notify(done)

    def action_ignore(self) -> None:
        self._row_action("item", self.core.ignore_item, "Ignored. Hidden until it's gone from the source")

    def action_acknowledge(self) -> None:
        self._row_action("health", self.core.acknowledge, "Acknowledged. Press c once it has recovered")

    def action_close(self) -> None:
        self._row_action("health", self.core.close, "Closed")

    def action_sync(self) -> None:
        self.run_worker(self.core.sync_now(), group="sync")
        self.notify("Syncing all sources")


def _status(source: SourceView) -> Text:
    if source.syncing:
        return Text("syncing…", style="italic")
    if source.last_error:
        return Text(f"error: {source.last_error}", style="bold red")
    if source.last_sync is None:
        return Text("never synced", style="dim")
    if source.health:
        failing = sum(h.state == FAILING and not h.acknowledged for h in source.health)
        text = Text(f"checked {_ago(source.last_sync)}", style="dim")
        if failing:
            text = Text(f"{failing} failing", style="bold red") + Text(f" · {text.plain}", style="dim")
        return text
    count = len(source.items)
    ignored = f" · {source.ignored} ignored" if source.ignored else ""
    return Text(f"{count} item{'s' * (count != 1)}{ignored} · synced {_ago(source.last_sync)}", style="dim")


def _health_row(health: HealthView, *, last: bool) -> tuple:
    label = Text("  └ " if last else "  ├ ", style="dim")
    if health.acknowledged:
        label.append("◐ ACKNOWLEDGED" if health.state == FAILING else "◐ RECOVERED", style="bold dark_orange")
        note = "you're on it" if health.state == FAILING else "press c to close"
        detail = Text(f"{health.summary} · {note}", style="dark_orange")
    elif health.state == FAILING:
        label.append("✗ FAILING", style="bold red")
        age = _ago(health.since)
        detail = Text(f"{health.summary} · failing for {'<1m' if age == 'just now' else age.removesuffix(' ago')}", style="red")
    else:
        label.append("✓ OK", style="bold green")
        detail = Text(f"{health.summary} · {health.failures}/{health.window} failed")
    return label, _truncate(health.key), detail, "", f"checked {_ago(health.checked_at)}"


def _truncate(text: str) -> str:
    return text if len(text) <= TITLE_WIDTH else text[: TITLE_WIDTH - 1] + "…"


def _parse_iso(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _ago(ts: float | None) -> str:
    if ts is None:
        return ""
    seconds = max(0, int(time.time() - ts))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit} ago"
    return "just now"
