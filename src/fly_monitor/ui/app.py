"""Terminal UI. Reads state from the core; never touches plugins or storage."""

from __future__ import annotations

import shutil
import subprocess
import webbrowser

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header

from fly_monitor.core.engine import Core
from fly_monitor.core.models import State
from fly_monitor.ui import layout
from fly_monitor.ui.forms import AddSourceScreen, ConfirmScreen, EditSourceScreen

class FlyMonitorApp(App):
    TITLE = "fly-monitor"
    CSS = "DataTable { height: 1fr; }"
    BINDINGS = [
        ("enter", "open", "Open"),
        ("r", "sync", "Sync now"),
        ("a", "add", "Add source"),
        ("e", "edit", "Edit source"),
        ("g", "grouping", "Group"),
        ("minus", "fold_level", "Fold level"),
        ("plus", "unfold_level", "Unfold level"),
        Binding("equals_sign", "unfold_level", "Unfold level", show=False),  # + without shift
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
        levels = dict(layout.PRESETS).get(core.preference("grouping"), layout.PRESETS[0][1])
        self._view = layout.View(levels=levels, depth=len(levels))

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(cursor_type="row", show_header=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(*["" for _ in range(layout.VALUE_COLUMNS + 3)])
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

        fixed = layout.WIDTHS["name"] + sum(layout.WIDTHS["value"]) + layout.WIDTHS["note"] + 2 * (layout.VALUE_COLUMNS + 3)
        self._view.title_width = max(20, self.size.width - fixed)
        plugins = {p.name: p for p in self.core.list_plugins()}
        table.clear()
        self._urls, self._rows = {}, {}
        if not state.sources:
            table.add_row(Text(" no sources yet ", style="reverse"), Text("press a to add one", style="dim"), *[""] * (layout.VALUE_COLUMNS + 1), key="none")
        for row in layout.build(state, plugins, self._view):
            table.add_row(*row.cells, key=row.key)
            if row.url:
                self._urls[row.key] = row.url
            if row.ref:
                self._rows[row.key] = ("health" if row.kind in ("health", "source") else row.kind, row.source_id, row.ref)
            elif row.source_id:
                self._rows[row.key] = (row.kind, row.source_id, "")

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
        key = key or ""
        if key.startswith("grp:"):
            self._view.folded_groups ^= {key}
            self._redraw()
            return
        if key.startswith(("hdr:", "more:")) and key not in self._urls:
            source_id = key.split(":", 1)[1]
            source = next((s for s in self.core.get_state().sources if s.id == source_id), None)
            if source is not None:
                default = not source.health
                self._view.expanded[source_id] = not self._view.expanded.get(source_id, default)
                self._redraw()
            return
        url = self._urls.get(key)
        if not url:
            return
        if shutil.which("xdg-open"):
            # Keep the browser's output from scribbling over the TUI.
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            webbrowser.open(url)
        self.notify(url, title="Opened")

    def _selected_source(self) -> str | None:
        row = self._selected_row()
        return row[1] if row else None

    def action_add(self) -> None:
        def added(source_ids: list[str] | None) -> None:
            for source_id in source_ids or []:
                self.run_worker(self.core.sync_now(source_id), group="sync")
            if source_ids:
                self.notify(f"Added {', '.join(source_ids)}, syncing")

        self.push_screen(AddSourceScreen(self.core), added)

    def action_edit(self) -> None:
        source_id = self._selected_source()
        if source_id is None:
            return
        try:
            screen = EditSourceScreen(self.core, source_id)
        except ValueError as exc:
            self.notify(str(exc), severity="warning")
            return

        def saved(yes: bool | None) -> None:
            if yes:
                self.notify("Saved. Applies from the next sync.")

        self.push_screen(screen, saved)

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
            self.notify(f"Select a {'GitHub item' if kind == 'item' else 'check (queue or website)'} row first", severity="warning")
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

    def action_grouping(self) -> None:
        names = [name for name, _ in layout.PRESETS]
        current = next((name for name, levels in layout.PRESETS if levels == self._view.levels), names[0])
        name = names[(names.index(current) + 1) % len(names)]
        self._view.levels = dict(layout.PRESETS)[name]
        self._view.folded_groups.clear()
        self._view.expanded = {}
        self._view.depth = len(self._view.levels)
        self.core.set_preference("grouping", name)
        self._redraw()
        self.notify(f"Grouped by {name}")

    def action_fold_level(self) -> None:
        self._set_depth(self._view.depth - 1)

    def action_unfold_level(self) -> None:
        self._set_depth(self._view.depth + 1)

    def _set_depth(self, depth: int) -> None:
        """Show the overview down to `depth`: one grouping level per step, then sources, then everything."""
        levels = self._view.levels
        depth = max(0, min(depth, len(levels) + 1))
        state = self.core.get_state()
        plugins = {p.name: p for p in self.core.list_plugins()}
        self._view.depth = depth
        self._view.folded_groups = {
            row.key
            for row in layout.build(state, plugins, layout.View(levels=levels))
            if row.kind == "group" and row.key.count("\x1f") >= depth  # a group's level is its depth in the path
        }
        if depth > len(levels):
            self._view.expanded = {s.id: True for s in state.sources}
        else:
            self._view.expanded = {}  # sources as usual: healthy on one line, problems shown
        self._redraw()
        self.notify(layout.depth_name(levels, depth), timeout=2)

    def action_sync(self) -> None:
        self.run_worker(self.core.sync_now(), group="sync")
        self.notify("Syncing all sources")
