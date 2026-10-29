import pytest

from watchkeep.core.connections import Unreachable
from watchkeep.core.plugin import SyncContext
from watchkeep.plugins.website import WebsiteSource, parse_codes


class FakeHttp:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def probe(self, url, *, timeout):
        self.calls.append((url, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def ctx(result, **settings):
    return SyncContext(settings={"url": "https://example.com", **settings}, http=FakeHttp(result))


def test_parse_codes():
    assert parse_codes("200-202, 301") == {200, 201, 202, 301}
    with pytest.raises(ValueError):
        parse_codes("ok")
    with pytest.raises(ValueError):
        parse_codes("700")


def test_healthy_check():
    [sample] = WebsiteSource().sync(ctx((200, 84)))
    assert sample.ok and sample.summary == "200 · 84 ms" and sample.key == "https://example.com"
    assert sample.values == {"status": 200, "response_ms": 84}


def test_status_outside_healthy_codes_fails():
    [sample] = WebsiteSource().sync(ctx((301, 10), healthy="200"))
    assert not sample.ok


def test_redirect_is_healthy_by_default():
    [sample] = WebsiteSource().sync(ctx((302, 10)))
    assert sample.ok


def test_unreachable_is_a_failed_check_not_an_error():
    c = ctx(Unreachable("timeout after 3s"), timeout="3")
    [sample] = WebsiteSource().sync(c)
    assert not sample.ok and sample.summary == "timeout after 3s"
    assert c.http.calls == [("https://example.com", 3.0)]


def test_test_connection_reports_failure_clearly():
    assert WebsiteSource().test_connection(ctx((200, 5))) == "200 · 5 ms"
    with pytest.raises(RuntimeError, match="500 · 5 ms: this check would count as failed"):
        WebsiteSource().test_connection(ctx((500, 5)))
