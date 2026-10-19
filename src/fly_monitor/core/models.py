"""Plain data that crosses the core/UI line. Everything here must stay JSON-friendly."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Item:
    """One thing a source reports, e.g. a review request.

    `id` identifies the item inside its source across snapshots. `updated_at`
    tells the core whether an item it already knows has moved.
    """

    id: str
    action: str
    title: str
    url: str
    repo: str
    author: str
    updated_at: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Item:
        return cls(**data)


@dataclass(frozen=True)
class Sample:
    """One measurement of something that is always there, e.g. a website.

    `key` names the row (a URL, a queue). `ok` is whether this single check
    passed the user's criteria; the core judges what that means over time.
    """

    key: str
    ok: bool
    summary: str  # short, human: "200 · 84 ms", "timeout after 10s"
    values: dict  # raw numbers, e.g. {"status": 200, "response_ms": 84}
    url: str = ""  # what Enter opens, if anything


@dataclass(frozen=True)
class HealthView:
    key: str
    state: str  # "ok" | "failing"
    since: float
    summary: str  # latest check
    failures: int  # failed checks in the current window
    window: int
    checked_at: float
    url: str
    acknowledged: bool = False


@dataclass(frozen=True)
class ItemView:
    item: Item
    first_seen: float
    is_new: bool  # appeared in the most recent sync


@dataclass(frozen=True)
class SourceView:
    id: str
    plugin: str
    name: str
    tags: tuple[str, ...]
    interval: int
    last_sync: float | None
    last_error: str | None
    syncing: bool
    items: tuple[ItemView, ...]
    health: tuple[HealthView, ...] = ()  # for sample-based sources
    ignored: int = 0  # items hidden by the user


@dataclass(frozen=True)
class State:
    # Bumped on every change, so a UI can skip redraws when nothing moved.
    version: int
    sources: tuple[SourceView, ...]
