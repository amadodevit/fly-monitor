"""Checks: user-editable conditions on the numbers a samples plugin reports.

The plugin declares its metrics and ships default checks. The core evaluates
them on every sample; a sample is a bad check when any enabled check fires.
Whether bad checks add up to "failing" is still the health rule's job.

    Check("Stuck", size > 0 and dequeued = 0)   -> fires on one sample
    Rule(3 of 3 bad)                            -> failing over time

A counter metric (it only goes up, like "messages dequeued since the broker
started") is compared by its growth since the previous sync, never its raw value.
"""

from __future__ import annotations

import operator
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .plugin import Metric

OPS = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "=": operator.eq,
    "!=": operator.ne,
}


@dataclass(frozen=True)
class Condition:
    metric: str
    op: str
    value: float

    def __post_init__(self) -> None:
        if self.op not in OPS:
            raise ValueError(f"unknown operator {self.op!r}: use one of {' '.join(OPS)}")


@dataclass(frozen=True)
class Check:
    id: str
    label: str
    conditions: tuple[Condition, ...]  # all must hold for the check to fire
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "enabled": self.enabled,
            "conditions": [[c.metric, c.op, c.value] for c in self.conditions],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Check:
        return cls(
            id=data["id"],
            label=data["label"],
            enabled=data.get("enabled", True),
            conditions=tuple(Condition(m, op, float(v)) for m, op, v in data["conditions"]),
        )


def validate(checks: Iterable[Check], metrics: Iterable[Metric]) -> None:
    known = {m.key for m in metrics}
    for check in checks:
        for condition in check.conditions:
            if condition.metric not in known:
                raise ValueError(f"check {check.label!r} uses unknown metric {condition.metric!r}")


def fired(
    checks: Iterable[Check],
    values: dict[str, Any],
    previous: dict[str, Any] | None,
    metrics: Iterable[Metric],
) -> list[str]:
    """Labels of the enabled checks that fire on this sample."""
    counters = {m.key for m in metrics if m.counter}
    seen = {key: _value(key, values, previous, counters) for key in values}
    return [
        check.label
        for check in checks
        if check.enabled and check.conditions and all(_holds(c, seen.get(c.metric)) for c in check.conditions)
    ]


def growth_summary(values: dict[str, Any], previous: dict[str, Any] | None, metrics: Iterable[Metric]) -> str:
    """How much each counter grew since the last sync, for the row: "+1.2k in · +0 out"."""
    parts = []
    for metric in metrics:
        if metric.counter and (grown := growth(metric.key, values, previous)) is not None:
            parts.append(f"+{compact(grown)} {metric.short or metric.label.lower()}")
    return " · ".join(parts)


def growth(key: str, values: dict[str, Any], previous: dict[str, Any] | None) -> float | None:
    current, before = values.get(key), (previous or {}).get(key)
    if not isinstance(current, int | float) or not isinstance(before, int | float):
        return None  # first sync: no growth known yet
    grown = current - before
    return grown if grown >= 0 else None  # negative: the counter was reset (e.g. broker restart)


def _value(key: str, values: dict[str, Any], previous: dict[str, Any] | None, counters: set[str]) -> float | None:
    if key in counters:
        return growth(key, values, previous)
    current = values.get(key)
    return current if isinstance(current, int | float) else None


def compact(n: float) -> str:
    """1234 -> "1.2k", 1500000 -> "1.5M"."""
    for size, suffix in ((1_000_000, "M"), (1_000, "k")):
        if abs(n) >= size:
            return f"{n / size:.1f}".rstrip("0").rstrip(".") + suffix
    return f"{n:g}"


def _holds(condition: Condition, value: float | None) -> bool:
    return value is not None and OPS[condition.op](value, condition.value)
