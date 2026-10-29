"""Compare a new snapshot with the previous one."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .models import Item


@dataclass(frozen=True)
class SnapshotDiff:
    new: list[Item]  # not in the previous snapshot (includes re-requests)
    updated: list[Item]  # known, but changed (e.g. updated_at moved)
    unchanged: list[Item]
    resolved: list[Item]  # in the previous snapshot, gone now


def diff_snapshot(previous: Mapping[str, Item], current: Iterable[Item]) -> SnapshotDiff:
    new: list[Item] = []
    updated: list[Item] = []
    unchanged: list[Item] = []
    seen: set[str] = set()

    for item in current:
        if item.id in seen:
            continue  # a plugin returned the same id twice; keep the first
        seen.add(item.id)
        before = previous.get(item.id)
        if before is None:
            new.append(item)
        elif before != item:
            updated.append(item)
        else:
            unchanged.append(item)

    resolved = [item for item_id, item in previous.items() if item_id not in seen]
    return SnapshotDiff(new=new, updated=updated, unchanged=unchanged, resolved=resolved)
