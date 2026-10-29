import json
from pathlib import Path

from watchkeep.core.plugin import SyncContext
from watchkeep.plugins.github import GitHubSource, api_to_html, parse_notification, parse_review_search

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


class FakeHttp:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get_json(self, url, *, headers=None, params=None):
        self.calls.append((url, headers))
        return self.routes[url]


def test_parse_review_search():
    items = parse_review_search(load("review_search.json"))
    first = items[0]
    assert first.id == "review:https://github.com/acme/widgets/pull/42"
    assert first.title == "Fix the wobbly widget"  # stripped
    assert first.repo == "acme/widgets"
    assert first.author == "alice"
    assert first.action == "review requested"


def test_parse_notification_with_comment_links_to_comment():
    thread = load("notifications.json")[0]
    item = parse_notification(thread, load("comment_555.json"))
    assert item.action == "commented"
    assert item.author == "carol"
    assert item.url.endswith("#issuecomment-555")
    assert item.id == "commented:https://github.com/acme/widgets/issues/9"


def test_parse_notification_ignores_other_reasons():
    assert parse_notification(load("notifications.json")[2], None) is None


def test_api_to_html():
    assert api_to_html("https://api.github.com/repos/a/b/pulls/3") == "https://github.com/a/b/pull/3"
    assert api_to_html("https://api.github.com/repos/a/b/issues/3") == "https://github.com/a/b/issues/3"
    assert api_to_html(None) is None


def test_sync_combines_reviews_and_comments_and_sends_token():
    http = FakeHttp(routes())
    items = GitHubSource().sync(SyncContext(settings={"account": "me", "token": "t0k"}, http=http))
    assert [i.action for i in items] == ["review requested", "review requested", "commented", "mentioned"]
    # same PR as review request and mention -> two rows, distinct ids
    assert len({i.id for i in items}) == 4
    assert all(h["Authorization"] == "Bearer t0k" for _, h in http.calls)


def routes(**states):
    return {
        "https://api.github.com/search/issues": load("review_search.json"),
        "https://api.github.com/notifications": load("notifications.json"),
        "https://api.github.com/repos/acme/widgets/issues/comments/555": load("comment_555.json"),
        "https://api.github.com/repos/acme/widgets/issues/9": {"state": states.get("issue9", "open")},
        "https://api.github.com/repos/acme/widgets/pulls/42": {"state": states.get("pr42", "open")},
    }


def sync_with(watch):
    http = FakeHttp(routes())
    items = GitHubSource().sync(SyncContext(settings={"account": "me", "token": "t", "watch": watch}, http=http))
    return items, [url for url, _ in http.calls]


def test_watch_reviews_only_skips_notifications():
    items, calls = sync_with(["review_requests"])
    assert {i.action for i in items} == {"review requested"}
    assert calls == ["https://api.github.com/search/issues"]


def test_watch_mentions_only_skips_search_and_comments():
    items, calls = sync_with(["mentions"])
    assert [i.action for i in items] == ["mentioned"]
    assert "https://api.github.com/search/issues" not in calls


def test_missing_watch_means_everything():
    items, _ = sync_with(None)
    assert {i.action for i in items} == {"review requested", "commented", "mentioned"}


def test_parse_gh_hosts_keeps_working_github_com_logins():
    from watchkeep.plugins.github import parse_gh_hosts

    found = parse_gh_hosts(load("gh_auth_status.json"))
    assert [c.name for c in found] == ["octo-work", "octo-home"]
    assert found[0].settings == {"account": "octo-work", "token": "gh:octo-work"}
    assert "active" in found[0].description


def test_comments_and_mentions_on_closed_subjects_are_hidden():
    http = FakeHttp(routes(pr42="closed"))  # the mentioned PR was merged
    items = GitHubSource().sync(SyncContext(settings={"account": "me", "token": "t", "watch": ["comments", "mentions"]}, http=http))
    assert [i.action for i in items] == ["commented"]


def test_failed_state_lookup_keeps_the_row():
    r = routes()
    del r["https://api.github.com/repos/acme/widgets/pulls/42"]  # FakeHttp raises KeyError
    items = GitHubSource().sync(SyncContext(settings={"account": "me", "token": "t", "watch": ["mentions"]}, http=FakeHttp(r)))
    assert [i.action for i in items] == ["mentioned"]


def test_subject_without_state_is_kept():
    from watchkeep.plugins.github import _is_closed

    thread = {"subject": {"url": "https://api.github.com/repos/a/b/releases/1"}}
    http = FakeHttp({})
    assert _is_closed(SyncContext(settings={}, http=http), {}, thread) is False
    assert http.calls == []
