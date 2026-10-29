"""Notification events and the built-in desktop channel.

The core records an Event whenever something deserves attention. The desktop
channel is sent by the core itself; UIs read the same events through
`Core.events_since()` and show them however they like (e.g. a toast).
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

NEW_ITEM = "new_item"
FAILING = "failing"
RECOVERED = "recovered"


@dataclass(frozen=True)
class Event:
    seq: int
    at: float
    kind: str  # NEW_ITEM | FAILING | RECOVERED
    source_id: str
    source_name: str
    title: str
    body: str
    url: str = ""


class DesktopChannel:
    """Desktop popups via notify-send. Does nothing where notify-send is missing."""

    def __init__(self, run: Callable = subprocess.run, which: Callable = shutil.which) -> None:
        self._run = run
        self._available = which("notify-send") is not None

    def send(self, events: list[Event]) -> None:
        """One popup per batch, so a busy sync doesn't flood the desktop."""
        if not events or not self._available:
            return
        urgent = any(e.kind == FAILING for e in events)
        if len(events) == 1:
            title, body = events[0].title, events[0].body
        else:
            what = "new" if all(e.kind == NEW_ITEM for e in events) else "changes"
            title = f"{len(events)} {what} on {events[0].source_name}"
            body = "\n".join(f"• {e.body}" for e in events[:5]) + ("\n…" if len(events) > 5 else "")
        try:
            self._run(
                ["notify-send", "--app-name=watchkeep", "-u", "critical" if urgent else "normal", title, body],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass  # a missing notification daemon must never break syncing
