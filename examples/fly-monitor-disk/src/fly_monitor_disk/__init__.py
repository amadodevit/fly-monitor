"""Example fly-monitor plugin: is any disk getting full?

A samples source: one row per path, checked every 5 minutes. The plugin only
says whether each check passed; the core decides when that means "failing".
"""

from __future__ import annotations

import shutil

from fly_monitor.core.health import Rule
from fly_monitor.core.models import Sample
from fly_monitor.core.plugin import Setting, SyncContext


class DiskSource:
    name = "disk"
    title = "Disks"
    connection = "http"  # unused: every plugin must name a core connection for now
    kind = "samples"
    default_interval = 300  # 5m
    default_rule = Rule(window=3, fail_after=3, recover_after=1)  # disks fill slowly; no need to wait long
    identity = ("paths",)
    settings = (
        Setting("paths", "Paths", required=False, default="/", help="comma separated, e.g. /, /home"),
        Setting("max_used", "Max used (%)", required=False, default="90"),
    )

    def sync(self, ctx: SyncContext) -> list[Sample]:
        limit = float(ctx.settings.get("max_used", 90))
        paths = [p.strip() for p in str(ctx.settings.get("paths", "/")).split(",") if p.strip()]
        return [check(path, limit) for path in paths]

    def test_connection(self, ctx: SyncContext) -> str:
        return "; ".join(sample.summary for sample in self.sync(ctx))


def check(path: str, limit: float, usage=shutil.disk_usage) -> Sample:
    try:
        total, used, free = usage(path)
    except OSError as exc:
        # A missing mount is a failed check, not a plugin error.
        return Sample(key=path, ok=False, summary=f"{path} · {exc.strerror}", values={"used_pct": None})
    used_pct = round(used / total * 100, 1)
    return Sample(
        key=path,
        ok=used_pct < limit,
        summary=f"{path} · {used_pct:g}% used · {free / 1e9:.0f} GB free",
        values={"used_pct": used_pct, "free_bytes": free},
    )
