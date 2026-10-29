import httpx
import pytest

from watchkeep.core.connections import HttpConnection


def connection(handler):
    conn = HttpConnection()
    conn._client = httpx.Client(transport=httpx.MockTransport(handler))
    return conn


def test_get_returns_the_response_with_headers_and_body():
    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://feeds.example.com/feed"})
        assert request.headers["If-None-Match"] == '"v1"'
        return httpx.Response(200, content=b"<rss/>", headers={"etag": '"v2"'})

    response = connection(handler).get("https://feeds.example.com/old", headers={"If-None-Match": '"v1"'})
    assert response.status_code == 200 and response.content == b"<rss/>"
    assert response.headers["etag"] == '"v2"'  # redirect followed


def test_get_passes_not_modified_through_and_raises_on_errors():
    statuses = iter([304, 404])
    conn = connection(lambda request: httpx.Response(next(statuses)))
    assert conn.get("https://feeds.example.com/feed").status_code == 304
    with pytest.raises(httpx.HTTPStatusError):
        conn.get("https://feeds.example.com/feed")
