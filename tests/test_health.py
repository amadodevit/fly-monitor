import pytest

from fly_monitor.core.health import FAILING, OK, Rule, step

RULE = Rule(window=10, fail_after=5, recover_after=3)


def run(results, health=None, rule=RULE):
    states = []
    for t, ok in enumerate(results):
        health = step(health, ok, float(t), rule)
        states.append(health.state)
    return health, states


def checks(text):
    return [c == "✓" for c in text.replace(" ", "")]


def test_four_failures_in_ten_stays_ok():
    health, _ = run(checks("✗ ✓ ✗ ✓ ✗ ✓ ✗ ✓ ✓ ✓"))
    assert health.state == OK and health.failures(RULE) == 4


def test_fifth_failure_in_window_turns_failing():
    _, states = run(checks("✗ ✓ ✗ ✓ ✗ ✓ ✗ ✓ ✗"))
    assert states[-1] == FAILING and states[-2] == OK


def test_old_failures_slide_out_of_the_window():
    health, states = run(checks("✗ ✗ ✗ ✗ ✓ ✓ ✓ ✓ ✓ ✓ ✓ ✓ ✓ ✓ ✗"))
    assert FAILING not in states  # the early ✗s left the window before the last one


def test_recovery_needs_three_good_in_a_row():
    failing, _ = run(checks("✗ ✗ ✗ ✗ ✗"))
    _, states = run(checks("✓ ✓ ✗ ✓ ✓ ✓"), health=failing)
    assert states == [FAILING, FAILING, FAILING, FAILING, FAILING, OK]


def test_conversation_trace_no_flap_after_recovery():
    # ✗ ✗ ✗ ✗ ✗ ✗ ✗ then ✓ ✓ ✓ recovers; one new ✗ must not re-fail it
    health, states = run(checks("✗ ✗ ✗ ✗ ✗ ✗ ✗ ✓ ✓ ✓ ✗"))
    assert states[-2] == OK and states[-1] == OK
    assert health.failures(RULE) == 1  # only checks since the recovery count


def test_failing_again_needs_a_full_new_count():
    health, states = run(checks("✗ ✗ ✗ ✗ ✗ ✓ ✓ ✓ ✗ ✗ ✗ ✗"))
    assert states[-1] == OK  # 4 failures since recovery
    health = step(health, False, 99.0, RULE)
    assert health.state == FAILING and health.since == 99.0


def test_since_marks_state_changes_only():
    health, _ = run(checks("✓ ✓ ✓"))
    assert health.since == 0.0  # still the first OK


@pytest.mark.parametrize("kwargs", [{"window": 0}, {"fail_after": 11, "window": 10}, {"recover_after": 0}])
def test_rule_validation(kwargs):
    with pytest.raises(ValueError):
        Rule(**kwargs)
