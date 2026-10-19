"""The contract a source plugin implements.

A plugin declares what it needs (name, connection type, settings) and returns a
full snapshot of what is currently true on each sync. It knows nothing about
storage, history, lifecycle or notifications: the core owns those.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .connections import HttpConnection
from .models import Item


@dataclass(frozen=True)
class Choice:
    value: str
    label: str
    default: bool = True


@dataclass(frozen=True)
class Setting:
    """One field a user fills in. The add-source form is generated from these.

    With `choices`, the setting is a multi-select and its value is a list of
    the ticked `Choice.value`s.
    """

    key: str
    label: str
    required: bool = True
    secret: bool = False  # masked in forms; "gh:<user>" / "env:VAR" references resolved before sync
    default: str | None = None
    help: str = ""
    choices: tuple[Choice, ...] = ()


@dataclass(frozen=True)
class Candidate:
    """A source a plugin found on this machine, ready to add."""

    name: str
    settings: dict[str, Any]
    description: str = ""


@dataclass
class SyncContext:
    settings: dict[str, Any]
    http: HttpConnection
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SourcePlugin(Protocol):
    name: str
    connection: str  # connection type the core must provide, e.g. "http"
    settings: tuple[Setting, ...]

    def sync(self, ctx: SyncContext) -> list[Item]:
        """Return everything currently true. Items missing from the result are resolved."""
        ...

    # Optional members (checked with hasattr/getattr, so not part of the Protocol check):
    #
    #   identity: tuple[str, ...]
    #       Setting keys that identify a source, e.g. ("account",). Lets the core
    #       tell that a detected candidate is already added.
    #
    #   def test_connection(self, ctx: SyncContext) -> str
    #       Return a short description on success, raise on failure.
    #
    #   def detect(self) -> list[Candidate]
    #       Look for sources on this machine (e.g. logged-in CLIs). Never raise
    #       for "nothing found"; return [].
