"""ActiveMQ Classic source: one row per queue, read through Jolokia (the broker's JSON-over-HTTP API).

The plugin only reports numbers. Whether a queue is in trouble is decided by the
checks the user keeps or edits when adding the source (no consumers, stuck,
backlog); the core evaluates them.
"""

from __future__ import annotations

import base64
import fnmatch
import re
from typing import Any
from urllib.parse import quote

from fly_monitor.core.checks import Check, Condition
from fly_monitor.core.health import Rule
from fly_monitor.core.models import Sample
from fly_monitor.core.plugin import Metric, Setting, SyncContext

QUEUES = "org.apache.activemq:type=Broker,brokerName=*,destinationType=Queue,destinationName=*"
BROKERS = "org.apache.activemq:type=Broker,brokerName=*"
ATTRIBUTES = {"QueueSize": "size", "ConsumerCount": "consumers", "EnqueueCount": "enqueued", "DequeueCount": "dequeued"}
_NAME = re.compile(r"destinationName=([^,]+)")


class ActiveMQSource:
    name = "activemq"
    connection = "http"
    kind = "samples"
    default_interval = 60
    default_rule = Rule(window=3, fail_after=3, recover_after=1)  # bad for 3 syncs in a row
    identity = ("url",)
    settings = (
        Setting("url", "Web console URL", help="e.g. http://localhost:8161"),
        Setting("user", "User", required=False, default="admin"),
        Setting("password", "Password", secret=True),
        Setting(
            "queues",
            "Queues",
            required=False,
            default="*",
            fetch=True,
            help="names or patterns, comma separated, e.g. orders.*, billing.invoices",
        ),
    )
    metrics = (
        Metric("size", "Messages waiting"),
        Metric("consumers", "Consumers"),
        Metric("enqueued", "Enqueued", counter=True, short="in"),
        Metric("dequeued", "Dequeued", counter=True, short="out"),
    )
    default_checks = (
        Check("no_consumers", "No consumers", (Condition("consumers", "<", 1),)),
        Check("stuck", "Stuck", (Condition("size", ">", 0), Condition("dequeued", "=", 0))),
        Check("backlog", "Backlog", (Condition("size", ">", 1000),)),
    )

    def sync(self, ctx: SyncContext) -> list[Sample]:
        patterns = [p.strip() for p in str(ctx.settings.get("queues", "*")).split(",") if p.strip()]
        base = _base(ctx)
        return [
            Sample(
                key=name,
                ok=True,  # the checks decide
                summary=f"{values['size']} waiting · {values['consumers']} consumer{'s' * (values['consumers'] != 1)}",
                values=values,
                url=f"{base}/admin/browse.jsp?JMSDestination={quote(name)}",
            )
            for name, values in sorted(read_queues(ctx).items())
            if any(fnmatch.fnmatchcase(name, p) for p in patterns)
        ]

    def options(self, ctx: SyncContext, key: str) -> list[str]:
        return sorted(read_queues(ctx)) if key == "queues" else []

    def test_connection(self, ctx: SyncContext) -> str:
        brokers = _jolokia(ctx, f"read/{BROKERS}/BrokerName,BrokerVersion")
        queues = len(read_queues(ctx))
        return "; ".join(
            f"broker {b['BrokerName']} · ActiveMQ {b['BrokerVersion']} · {queues} queue{'s' * (queues != 1)}"
            for b in brokers.values()
        )


def read_queues(ctx: SyncContext) -> dict[str, dict[str, int]]:
    """{queue name: {size, consumers, enqueued, dequeued}} for every queue on the broker."""
    try:
        data = _jolokia(ctx, f"read/{QUEUES}/{','.join(ATTRIBUTES)}")
    except NoQueues:
        return {}
    return parse_queues(data)


def parse_queues(value: dict[str, Any]) -> dict[str, dict[str, int]]:
    queues = {}
    for mbean, attributes in value.items():
        match = _NAME.search(mbean)
        if match:
            queues[match[1]] = {key: attributes[attr] for attr, key in ATTRIBUTES.items()}
    return queues


class NoQueues(Exception):
    pass


def _jolokia(ctx: SyncContext, path: str) -> Any:
    data = ctx.http.get_json(f"{_base(ctx)}/api/jolokia/{path}", headers=_headers(ctx))
    if data.get("status") == 404 and "InstanceNotFound" in data.get("error_type", ""):
        raise NoQueues  # a wildcard that matches nothing
    if data.get("status") != 200:
        raise RuntimeError(f"Jolokia: {data.get('error', 'unexpected answer')}")
    return data["value"]


def _base(ctx: SyncContext) -> str:
    return str(ctx.settings["url"]).rstrip("/")


def _headers(ctx: SyncContext) -> dict[str, str]:
    credentials = f"{ctx.settings.get('user', 'admin')}:{ctx.settings['password']}"
    return {
        "Authorization": "Basic " + base64.b64encode(credentials.encode()).decode(),
        "Origin": _base(ctx),  # Jolokia refuses requests without an Origin
    }
