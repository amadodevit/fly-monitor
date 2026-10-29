import pytest

from watchkeep.core.checks import Check, Condition, fired, growth_summary, validate
from watchkeep.core.plugin import Metric

METRICS = (Metric("size", "Size"), Metric("consumers", "Consumers"), Metric("dequeued", "Dequeued", counter=True))
STUCK = Check("stuck", "Stuck", (Condition("size", ">", 0), Condition("dequeued", "=", 0)))
NO_CONSUMERS = Check("none", "No consumers", (Condition("consumers", "<", 1),))


def test_gauge_condition_fires_on_the_value():
    assert fired([NO_CONSUMERS], {"consumers": 0}, None, METRICS) == ["No consumers"]
    assert fired([NO_CONSUMERS], {"consumers": 2}, None, METRICS) == []


def test_counter_is_judged_by_growth_since_last_sync():
    stuck_now = {"size": 4, "dequeued": 100}
    assert fired([STUCK], stuck_now, {"size": 3, "dequeued": 100}, METRICS) == ["Stuck"]
    assert fired([STUCK], stuck_now, {"size": 3, "dequeued": 90}, METRICS) == []


def test_counter_without_history_or_after_reset_never_fires():
    assert fired([STUCK], {"size": 4, "dequeued": 100}, None, METRICS) == []  # first sync
    assert fired([STUCK], {"size": 4, "dequeued": 0}, {"size": 4, "dequeued": 100}, METRICS) == []  # broker restart


def test_all_conditions_must_hold_and_disabled_checks_are_skipped():
    assert fired([STUCK], {"size": 0, "dequeued": 5}, {"dequeued": 5}, METRICS) == []
    off = Check.from_dict({**NO_CONSUMERS.to_dict(), "enabled": False})
    assert fired([off], {"consumers": 0}, None, METRICS) == []


def test_round_trip_and_validation():
    assert Check.from_dict(STUCK.to_dict()) == STUCK
    with pytest.raises(ValueError, match="unknown metric"):
        validate([Check("x", "X", (Condition("latency", ">", 1),))], METRICS)
    with pytest.raises(ValueError, match="unknown operator"):
        Condition("size", "=>", 1)


def test_growth_summary_names_counters_and_compacts_numbers():
    metrics = (Metric("size", "Size"), Metric("enqueued", "Enqueued", counter=True, short="in"), Metric("dequeued", "Dequeued", counter=True))
    now = {"size": 3, "enqueued": 1_925_494, "dequeued": 1_500_087}
    assert growth_summary(now, {"size": 1, "enqueued": 1_924_294, "dequeued": 87}, metrics) == "+1.2k in · +1.5M dequeued"
    assert growth_summary(now, {"enqueued": 1_925_407, "dequeued": 1_500_087}, metrics) == "+87 in · +0 dequeued"
    assert growth_summary(now, None, metrics) == ""  # first sync
    assert growth_summary(now, {"enqueued": 2_000_000, "dequeued": 1_500_000}, metrics) == "+87 dequeued"  # "in" was reset
