import pytest

from fly_monitor.core.config import DEFAULT_INTERVAL, parse_config, parse_interval


def test_parse_interval():
    assert parse_interval(90) == 90
    assert parse_interval("30m") == 1800
    assert parse_interval("1h") == 3600
    assert parse_interval("45") == 45
    with pytest.raises(ValueError):
        parse_interval("soon")


def test_parse_config_defaults_and_duplicates():
    [s] = parse_config({"source": [{"id": "a", "plugin": "github", "settings": {"account": "me"}}]})
    assert s.interval == DEFAULT_INTERVAL and s.name == "a"
    with pytest.raises(ValueError, match="duplicate"):
        parse_config({"source": [{"id": "a", "plugin": "g"}, {"id": "a", "plugin": "g"}]})
