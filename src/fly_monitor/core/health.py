"""Judging health over time. Pure functions, no I/O.

A plugin says whether each single check passed. This module decides what that
means over time:

    ok ──(fail_after of the last `window` checks failed)──► failing
    failing ──(recover_after good checks in a row)──► ok

After a recovery the history restarts, so failures from before it can't push
the source straight back into failing (no flapping).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

OK = "ok"
FAILING = "failing"


@dataclass(frozen=True)
class Rule:
    window: int = 10
    fail_after: int = 5
    recover_after: int = 3

    def __post_init__(self) -> None:
        if min(self.window, self.fail_after, self.recover_after) < 1:
            raise ValueError("rule numbers must be at least 1")
        if self.fail_after > self.window:
            raise ValueError(f"can't need {self.fail_after} failures within only {self.window} checks")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> Rule:
        return cls(**data) if data else cls()


@dataclass(frozen=True)
class Health:
    state: str
    since: float  # when the current state began
    recent: tuple[bool, ...]  # check results since the last recovery, oldest first, capped
    acknowledged: bool = False  # someone is on it; stays set after recovery until closed

    def failures(self, rule: Rule) -> int:
        return self.recent[-rule.window :].count(False)

    def to_dict(self) -> dict:
        return {"state": self.state, "since": self.since, "recent": list(self.recent), "acknowledged": self.acknowledged}

    @classmethod
    def from_dict(cls, data: dict) -> Health:
        return cls(
            state=data["state"],
            since=data["since"],
            recent=tuple(data["recent"]),
            acknowledged=data.get("acknowledged", False),
        )


def step(health: Health | None, ok: bool, at: float, rule: Rule) -> Health:
    """Fold one check result into the health state."""
    if health is None:
        health = Health(OK, at, ())
    recent = health.recent + (ok,)

    if health.state == OK:
        window = recent[-rule.window :]
        if window.count(False) >= rule.fail_after:
            return Health(FAILING, at, window)  # a new outage needs a new acknowledgment
        return Health(OK, health.since, window, health.acknowledged)

    if _good_streak(recent) >= rule.recover_after:
        return Health(OK, at, (), health.acknowledged)  # history restarts at recovery
    return Health(FAILING, health.since, recent[-max(rule.window, rule.recover_after) :], health.acknowledged)


def _good_streak(results: tuple[bool, ...]) -> int:
    streak = 0
    for result in reversed(results):
        if not result:
            break
        streak += 1
    return streak
