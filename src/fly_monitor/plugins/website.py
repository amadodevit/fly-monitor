"""Website source: is a URL answering with a healthy status, and how fast?

One check per sync. A timeout or refused connection is a failed check, not a
plugin error. Whether failures add up to "failing" is the core's call.
"""

from __future__ import annotations

from fly_monitor.core.connections import Unreachable
from fly_monitor.core.health import Rule
from fly_monitor.core.models import Sample
from fly_monitor.core.plugin import Metric, Setting, SyncContext


class WebsiteSource:
    name = "website"
    title = "Websites"
    connection = "http"
    kind = "samples"
    default_interval = 300  # 5m
    default_rule = Rule(window=10, fail_after=5, recover_after=3)
    identity = ("url",)
    metrics = (Metric("status", "Status"), Metric("response_ms", "Response time", short="ms"))
    settings = (
        Setting("url", "URL", help="e.g. https://example.com/health"),
        Setting(
            "healthy",
            "Healthy status codes",
            required=False,
            default="200-399",
            help="codes or ranges, e.g. 200-299,301. Redirects are not followed.",
        ),
        Setting("timeout", "Timeout (seconds)", required=False, default="10"),
    )

    def sync(self, ctx: SyncContext) -> list[Sample]:
        return [check(ctx)]

    def test_connection(self, ctx: SyncContext) -> str:
        sample = check(ctx)
        if not sample.ok:
            raise RuntimeError(f"{sample.summary}: this check would count as failed")
        return sample.summary


def check(ctx: SyncContext) -> Sample:
    url = ctx.settings["url"]
    healthy = parse_codes(str(ctx.settings.get("healthy", "200-399")))
    timeout = float(ctx.settings.get("timeout", 10))
    try:
        status, ms = ctx.http.probe(url, timeout=timeout)
    except Unreachable as exc:
        return Sample(key=url, ok=False, summary=str(exc), values={"status": None, "response_ms": None}, url=url)
    return Sample(
        key=url,
        ok=status in healthy,
        summary=f"{status} · {ms} ms",
        values={"status": status, "response_ms": ms},
        url=url,
    )


def parse_codes(spec: str) -> set[int]:
    codes: set[int] = set()
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        low, _, high = part.partition("-")
        try:
            start, end = int(low), int(high or low)
        except ValueError:
            raise ValueError(f"bad status code {part!r}: use e.g. 200-299,301") from None
        if not 100 <= start <= end <= 599:
            raise ValueError(f"bad status code range {part!r}")
        codes.update(range(start, end + 1))
    if not codes:
        raise ValueError("no healthy status codes given")
    return codes
