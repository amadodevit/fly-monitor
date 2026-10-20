"""Keeps the example plugin in examples/ working as the contract changes."""

import sys
from pathlib import Path

from fly_monitor.core.plugin import SourcePlugin, SyncContext

sys.path.insert(0, str(Path(__file__).parents[1] / "examples" / "fly-monitor-disk" / "src"))
from fly_monitor_disk import DiskSource, check  # noqa: E402


def test_satisfies_the_contract():
    assert isinstance(DiskSource(), SourcePlugin)


def test_one_sample_per_path():
    samples = DiskSource().sync(SyncContext(settings={"paths": "/, /", "max_used": "100"}, http=None))
    assert [s.key for s in samples] == ["/", "/"]
    assert all(s.ok for s in samples)


def test_over_the_limit_fails():
    sample = check("/data", 90, usage=lambda path: (100, 95, 5))
    assert not sample.ok and sample.summary.startswith("/data · 95% used")


def test_missing_path_is_a_failed_check():
    sample = check("/nope", 90, usage=lambda path: (_ for _ in ()).throw(FileNotFoundError(2, "No such file")))
    assert not sample.ok and "No such file" in sample.summary
