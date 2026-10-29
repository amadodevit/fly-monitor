import json
from pathlib import Path

import pytest

from watchkeep.core.plugin import SourcePlugin, SyncContext
from watchkeep.plugins.activemq import ActiveMQSource

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "activemq_queues.json").read_text())


class FakeJolokia:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def get_json(self, url, *, headers=None, params=None):
        self.calls.append((url, headers))
        return self.answer


def ctx(answer=FIXTURE, **settings):
    base = {"url": "http://broker:8161/", "user": "admin", "password": "secret", "queues": "*"}
    return SyncContext(settings={**base, **settings}, http=FakeJolokia(answer))


def test_satisfies_the_contract():
    assert isinstance(ActiveMQSource(), SourcePlugin)


def test_one_sample_per_queue_with_its_numbers():
    samples = ActiveMQSource().sync(ctx())
    assert [s.key for s in samples] == ["billing.invoices", "orders.created", "orders.paid"]
    created = samples[1]
    assert created.values == {"size": 3, "consumers": 0, "enqueued": 3, "dequeued": 0}
    assert created.summary == "3 waiting · 0 consumers"
    assert created.url == "http://broker:8161/admin/browse.jsp?JMSDestination=orders.created"


def test_patterns_pick_queues():
    samples = ActiveMQSource().sync(ctx(queues="orders.*, billing.invoices"))
    assert len(samples) == 3
    assert [s.key for s in ActiveMQSource().sync(ctx(queues="orders.p*"))] == ["orders.paid"]


def test_sends_basic_auth_and_origin():
    c = ctx()
    ActiveMQSource().sync(c)
    url, headers = c.http.calls[0]
    assert url.startswith("http://broker:8161/api/jolokia/read/")
    assert headers["Authorization"] == "Basic YWRtaW46c2VjcmV0"
    assert headers["Origin"] == "http://broker:8161"


def test_no_queues_is_empty_not_an_error():
    answer = {"status": 404, "error_type": "javax.management.InstanceNotFoundException", "error": "none"}
    assert ActiveMQSource().sync(ctx(answer)) == []


def test_jolokia_errors_are_raised():
    with pytest.raises(RuntimeError, match="Jolokia: denied"):
        ActiveMQSource().sync(ctx({"status": 403, "error": "denied"}))


def test_options_lists_queue_names():
    assert ActiveMQSource().options(ctx(), "queues") == ["billing.invoices", "orders.created", "orders.paid"]
