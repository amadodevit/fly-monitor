"""GitHub source: open review requests, plus unread comments and mentions.

Review requests come from search (a true snapshot: once you review, the PR drops
out). Comments and mentions come from unread notifications (they drop out when
marked read on GitHub).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

from fly_monitor.core.models import Item
from fly_monitor.core.plugin import Candidate, Choice, Setting, SyncContext

API = "https://api.github.com"
REVIEW_QUERY = "is:pr is:open review-requested:@me archived:false"
NOTIFICATION_ACTIONS = {"comment": "commented", "mention": "mentioned", "team_mention": "mentioned"}
# notification reason -> the "watch" choice that enables it
REASON_WATCH = {"comment": "comments", "mention": "mentions", "team_mention": "mentions"}
WATCH_ALL = ("review_requests", "comments", "mentions")


class GitHubSource:
    name = "github"
    connection = "http"
    settings = (
        Setting("account", "GitHub username", help="The login this source watches, e.g. octocat"),
        Setting(
            "token",
            "Token",
            secret=True,
            help="gh:<user> reads it from the GitHub CLI (nothing stored); or paste a token; or env:VAR",
        ),
        Setting(
            "watch",
            "Watch",
            choices=(
                Choice("review_requests", "Review requests"),
                Choice("comments", "Comments"),
                Choice("mentions", "Mentions"),
            ),
        ),
    )
    identity = ("account",)

    def detect(self) -> list[Candidate]:
        if not shutil.which("gh"):
            return []
        result = subprocess.run(["gh", "auth", "status", "--json", "hosts"], capture_output=True, text=True, timeout=10)
        try:
            return parse_gh_hosts(json.loads(result.stdout))
        except (json.JSONDecodeError, AttributeError):
            return []  # older gh without --json, or not logged in

    def sync(self, ctx: SyncContext) -> list[Item]:
        watch = set(ctx.settings.get("watch") or WATCH_ALL)  # sources from before "watch" existed get everything
        headers = _headers(ctx.settings["token"])
        items: list[Item] = []
        if "review_requests" in watch:
            search = ctx.http.get_json(f"{API}/search/issues", headers=headers, params={"q": REVIEW_QUERY, "per_page": 100})
            items += parse_review_search(search)

        if not watch & {"comments", "mentions"}:
            return items
        notifications = ctx.http.get_json(f"{API}/notifications", headers=headers, params={"per_page": 50})
        for thread in notifications:
            if REASON_WATCH.get(thread.get("reason", "")) not in watch:
                continue
            if _is_closed(ctx, headers, thread):
                continue  # merged/closed: nothing left to act on, even if the notification is unread
            comment = None
            if comment_url := thread["subject"].get("latest_comment_url"):
                try:
                    comment = ctx.http.get_json(comment_url, headers=headers)
                except Exception:
                    comment = None  # still show the row, just without author/deep link
            if item := parse_notification(thread, comment):
                items.append(item)
        return items

    def test_connection(self, ctx: SyncContext) -> str:
        user = ctx.http.get_json(f"{API}/user", headers=_headers(ctx.settings["token"]))
        login = user["login"]
        expected = ctx.settings["account"]
        if login.lower() != expected.lower():
            return f"authenticated as {login}, but config says {expected}"
        return f"authenticated as {login}"


def parse_gh_hosts(data: dict[str, Any]) -> list[Candidate]:
    candidates = []
    for account in data.get("hosts", {}).get("github.com", []):
        if account.get("state") != "success" or not account.get("login"):
            continue
        login = account["login"]
        candidates.append(
            Candidate(
                name=login,
                settings={"account": login, "token": f"gh:{login}"},
                description="logged in with gh" + (" (active)" if account.get("active") else ""),
            )
        )
    return candidates


def _is_closed(ctx: SyncContext, headers: dict[str, str], thread: dict[str, Any]) -> bool:
    """True when the thread's PR or issue is closed (merged PRs are closed too).

    Subjects without a state (discussions, releases, commits) and failed lookups
    count as open: better a stale row than a silently hidden one.
    """
    url = thread["subject"].get("url") or ""
    if not re.search(r"/(pulls|issues)/\d+$", url):
        return False
    try:
        return ctx.http.get_json(url, headers=headers).get("state") == "closed"
    except Exception:
        return False


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def parse_review_search(data: dict[str, Any]) -> list[Item]:
    items = []
    for pr in data.get("items", []):
        url = pr["html_url"]
        items.append(
            Item(
                id=f"review:{url}",
                action="review requested",
                title=pr["title"].strip(),
                url=url,
                repo=_repo_from_api(pr["repository_url"]),
                author=pr["user"]["login"],
                updated_at=pr["updated_at"],
            )
        )
    return items


def parse_notification(thread: dict[str, Any], comment: dict[str, Any] | None) -> Item | None:
    action = NOTIFICATION_ACTIONS.get(thread.get("reason", ""))
    if action is None:
        return None
    subject = thread["subject"]
    subject_url = api_to_html(subject.get("url")) or thread["repository"]["html_url"]
    link = (comment or {}).get("html_url") or subject_url
    return Item(
        id=f"{action}:{subject_url}",
        action=action,
        title=subject["title"].strip(),
        url=link,
        repo=thread["repository"]["full_name"],
        author=((comment or {}).get("user") or {}).get("login", ""),
        updated_at=thread["updated_at"],
    )


_API_PATH = re.compile(r"^https://api\.github\.com/repos/([^/]+/[^/]+)/(pulls|issues|commits)/(.+)$")


def api_to_html(url: str | None) -> str | None:
    if not url:
        return None
    match = _API_PATH.match(url)
    if not match:
        return None
    repo, kind, rest = match.groups()
    kind = {"pulls": "pull", "commits": "commit"}.get(kind, kind)
    return f"https://github.com/{repo}/{kind}/{rest}"


def _repo_from_api(url: str) -> str:
    return url.removeprefix(f"{API}/repos/")
