"""The panel manager (p): create, rename, reorder, delete and edit panels.

Edits happen on a copy and reach the core only on Save, through
`Core.save_panels`, so Escape always leaves things as they were. Rule values
are offered from what sources already use, and a live count says how many
sources the panel would show, so a misspelt client is caught before saving.
"""

from __future__ import annotations

from dataclasses import replace

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.suggester import SuggestFromList
from textual.widgets import Button, Input, Label, OptionList, SelectionList, Static
from textual.widgets.selection_list import Selection

from watchkeep.core.engine import Core
from watchkeep.core.panels import Panel, is_all, panel_matches
from watchkeep.ui.forms import DIALOG_CSS

# PanelFilter field -> label in the form
RULES = (("clients", "Client is"), ("environments", "Environment is"), ("tags", "Tag is"), ("plugins", "Type is"))


class ListSuggester(SuggestFromList):
    """Completes the last value of a comma-separated list: "globex, ac" -> "globex, acme"."""

    def __init__(self, values) -> None:
        super().__init__(values, case_sensitive=False)
        self._values = list(values)

    async def get_suggestion(self, value: str) -> str | None:
        head, sep, last = value.rpartition(",")
        part = last.lstrip()
        if not part:
            return None
        found = next((v for v in self._values if v.casefold().startswith(part.casefold())), None)
        return None if found is None else head + sep + last[: len(last) - len(part)] + found


def new_panel_id(panels: list[Panel]) -> str:
    taken = {p.id for p in panels}
    return next(f"p{n}" for n in range(1, len(taken) + 2) if f"p{n}" not in taken)


def _values(text: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in text.split(",") if v.strip())


class PanelsScreen(ModalScreen[bool]):
    # The form is taller than most terminals: the dialog takes a fixed height so
    # #body scrolls inside it and Save/Cancel stay on screen.
    DEFAULT_CSS = "PanelsScreen { align: center middle; }\n" + DIALOG_CSS + """
PanelsScreen #dialog { height: 90%; }
PanelsScreen #dialog #body { height: 1fr; }
#dialog #panel-buttons { height: auto; }
#dialog #count { margin-top: 1; text-style: bold; }
"""
    BINDINGS = [("ctrl+s", "save", "Save"), ("escape", "cancel", "Cancel")]

    def __init__(self, core: Core) -> None:
        super().__init__()
        self.core = core
        self._panels = core.panels()
        self._index = 0

    def compose(self) -> ComposeResult:
        sources = self.core.get_state().sources
        in_use = {
            "clients": sorted({s.client for s in sources if s.client}),
            "environments": sorted({s.environment for s in sources if s.environment}),
            "tags": sorted({t for s in sources for t in s.tags}),
            "plugins": sorted(p.name for p in self.core.list_plugins()),
        }
        with Vertical(id="dialog"):
            yield Static("Panels", id="title")
            with VerticalScroll(id="body"):
                yield Static("A source shows in a panel when it matches every rule you fill in (values: any of, "
                             "comma-separated), or when you tick it below.", classes="help")
                yield OptionList(*[p.name for p in self._panels], id="panels")
                with Horizontal(id="panel-buttons"):
                    yield Button("New", id="new")
                    yield Button("Up", id="up")
                    yield Button("Down", id="down")
                    yield Button("Delete", id="delete", variant="error")
                yield Label("Name", classes="field")
                yield Input(id="name")
                for key, label in RULES:
                    yield Label(label, classes="field")
                    yield Input(id=key, placeholder=", ".join(in_use[key]) or "none in use yet",
                                suggester=ListSuggester(in_use[key]))
                yield Label("Always include", classes="field")
                yield SelectionList(*[Selection(s.name, s.id) for s in sorted(sources, key=lambda s: s.name.lower())], id="picks")
                yield Static("", id="count")
                yield Static("", id="result")
            with Horizontal(id="buttons"):
                yield Button("Save (ctrl+s)", id="save", variant="primary")
                yield Button("Cancel (esc)", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#panels", OptionList).highlighted = 0
        self._load()

    # -- editor ------------------------------------------------------------------

    def _load(self) -> None:
        """Fill the editor from the highlighted panel."""
        panel = self._panels[self._index]
        self.query_one("#name", Input).value = panel.name
        for key, _ in RULES:
            field = self.query_one(f"#{key}", Input)
            field.value = ", ".join(getattr(panel.filter, key))
            field.disabled = is_all(panel)  # "All" shows everything; it has no rules
        picks = self.query_one("#picks", SelectionList)
        picks.deselect_all()
        for source_id in panel.filter.picks:
            picks.select(source_id)
        picks.disabled = is_all(panel)
        self._show_count()

    def _store(self) -> None:
        """Write the editor back into the highlighted panel."""
        panel = self._panels[self._index]
        name = self.query_one("#name", Input).value.strip() or panel.name
        if is_all(panel):
            self._panels[self._index] = replace(panel, name=name)
        else:
            rules = {key: _values(self.query_one(f"#{key}", Input).value) for key, _ in RULES}
            picks = tuple(self.query_one("#picks", SelectionList).selected)
            self._panels[self._index] = Panel(panel.id, name, replace(panel.filter, **rules, picks=picks))
        self.query_one("#panels", OptionList).replace_option_prompt_at_index(self._index, name)
        self._show_count()

    def _show_count(self) -> None:
        panel = self._panels[self._index]
        n = sum(panel_matches(panel, s) for s in self.core.get_state().sources)
        self.query_one("#count", Static).update(f"{n} source matches" if n == 1 else f"{n} sources match")

    def _say(self, text: str) -> None:
        self.query_one("#result", Static).update(Text(text, style="yellow"))

    def _relist(self, index: int) -> None:
        names = self.query_one("#panels", OptionList)
        names.clear_options()
        names.add_options([p.name for p in self._panels])
        self._index = index
        names.highlighted = index
        self._load()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_index != self._index:
            self._index = event.option_index
            self._load()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._store()

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        self._store()

    # -- buttons -----------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        handler = getattr(self, f"_{event.button.id}", None)
        if handler:
            handler()

    def _new(self) -> None:
        self._panels.append(Panel(new_panel_id(self._panels), "New panel"))
        self._relist(len(self._panels) - 1)
        self.query_one("#name", Input).focus()

    def _move(self, by: int) -> None:
        i, j = self._index, self._index + by
        if is_all(self._panels[i]) or not 1 <= j < len(self._panels):
            return  # "All" stays first
        self._panels[i], self._panels[j] = self._panels[j], self._panels[i]
        self._relist(j)

    def _up(self) -> None:
        self._move(-1)

    def _down(self) -> None:
        self._move(1)

    def _delete(self) -> None:
        panel = self._panels[self._index]
        if is_all(panel):
            self._say("'All' can't be deleted: it is where every source shows.")
            return
        del self._panels[self._index]
        self._relist(min(self._index, len(self._panels) - 1))
        self._say(f"Deleted '{panel.name}'. Its sources are kept. Save to apply.")

    def _save(self) -> None:
        self.action_save()

    def _cancel(self) -> None:
        self.action_cancel()

    def action_save(self) -> None:
        self.core.save_panels(self._panels)
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
