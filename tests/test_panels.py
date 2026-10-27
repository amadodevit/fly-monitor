"""Panel filters: fields AND, values OR, picks always in, stored as versioned JSON."""

from __future__ import annotations

import json

import pytest

from fly_monitor.core.models import SourceView
from fly_monitor.core.panels import (
    ALL_ID,
    VERSION,
    Panel,
    PanelFilter,
    all_panel,
    default_panels,
    from_json,
    is_all,
    matches,
    panel_matches,
    to_json,
)


def view(
    id: str,
    *,
    plugin: str = "github",
    client: str = "",
    environment: str = "",
    tags: tuple[str, ...] = (),
) -> SourceView:
    return SourceView(
        id=id,
        plugin=plugin,
        name=id,
        tags=tags,
        interval=300,
        last_sync=None,
        last_error=None,
        syncing=False,
        items=(),
        client=client,
        environment=environment,
    )


ACME_PROD = view("acme-prod", plugin="uptime", client="acme", environment="prod", tags=("web",))
ACME_STAGING = view("acme-staging", plugin="uptime", client="acme", environment="staging")
GLOBEX_PROD = view("globex-prod", plugin="activemq", client="globex", environment="prod")
PERSONAL = view("personal-gh", plugin="github", tags=("personal", "code"))
FEEDS = view("reading", plugin="feeds")
SOURCES = (ACME_PROD, ACME_STAGING, GLOBEX_PROD, PERSONAL, FEEDS)


def ids(f: PanelFilter) -> set[str]:
    return {s.id for s in SOURCES if matches(f, s)}


# --- one field at a time ---


def test_client_alone():
    assert ids(PanelFilter(clients=("acme",))) == {"acme-prod", "acme-staging"}


def test_environment_alone():
    assert ids(PanelFilter(environments=("prod",))) == {"acme-prod", "globex-prod"}


def test_tag_alone():
    assert ids(PanelFilter(tags=("personal",))) == {"personal-gh"}


def test_plugin_type_alone():
    assert ids(PanelFilter(plugins=("feeds",))) == {"reading"}


# --- combining ---


def test_fields_are_and():
    # acme's staging source is excluded: it fails the environment rule.
    assert ids(PanelFilter(clients=("acme",), environments=("prod",))) == {"acme-prod"}


def test_values_within_a_field_are_or():
    assert ids(PanelFilter(clients=("acme", "globex"))) == {
        "acme-prod",
        "acme-staging",
        "globex-prod",
    }
    assert ids(PanelFilter(tags=("web", "personal"))) == {"acme-prod", "personal-gh"}


def test_or_within_and_across():
    f = PanelFilter(clients=("acme", "globex"), environments=("prod",))
    assert ids(f) == {"acme-prod", "globex-prod"}


def test_case_and_whitespace_ignored_in_rules():
    assert ids(PanelFilter(clients=("  ACME ",))) == {"acme-prod", "acme-staging"}
    assert ids(PanelFilter(tags=(" Personal",), plugins=("GitHub ",))) == {"personal-gh"}


def test_case_and_whitespace_ignored_in_source_values():
    messy = view("messy", plugin=" Uptime", client=" Acme  ", environment="PROD ", tags=(" Web ",))
    f = PanelFilter(clients=("acme",), environments=("prod",), tags=("web",), plugins=("uptime",))
    assert matches(f, messy)


def test_blank_rule_values_do_not_constrain():
    # A rule value that is only whitespace is treated as not set; it never
    # turns into "client is empty".
    assert ids(PanelFilter(clients=("acme", "  "))) == {"acme-prod", "acme-staging"}
    assert ids(PanelFilter(clients=(" ",))) == set()


# --- hand-picked sources ---


def test_picks_always_included_even_when_rules_exclude():
    f = PanelFilter(clients=("acme",), picks=("reading",))
    assert ids(f) == {"acme-prod", "acme-staging", "reading"}


def test_picks_only_panel():
    f = PanelFilter(picks=("personal-gh", "globex-prod"))
    assert ids(f) == {"personal-gh", "globex-prod"}


def test_pick_of_unknown_source_matches_nothing_else():
    assert ids(PanelFilter(picks=("gone",))) == set()


# --- edge cases and the built-in panel ---


def test_empty_filter_matches_nothing():
    assert ids(PanelFilter()) == set()


def test_all_panel_matches_everything():
    panel = all_panel()
    assert panel.id == ALL_ID
    assert is_all(panel)
    assert all(panel_matches(panel, s) for s in SOURCES)


def test_panel_matches_uses_the_filter_for_user_panels():
    panel = Panel(id="p1", name="Acme", filter=PanelFilter(clients=("acme",)))
    assert not is_all(panel)
    assert {s.id for s in SOURCES if panel_matches(panel, s)} == {"acme-prod", "acme-staging"}


def test_default_panels_is_just_all():
    assert default_panels() == [all_panel()]


# --- storage ---


def test_json_round_trip():
    panels = [
        all_panel(),
        Panel(id="p1", name="Acme prod", filter=PanelFilter(clients=("acme",), environments=("prod",))),
        Panel(id="p2", name="Morning", filter=PanelFilter(picks=("personal-gh", "reading"))),
        Panel(id="p3", name="Reading", filter=PanelFilter(plugins=("feeds",), tags=("personal",))),
    ]
    text = to_json(panels)
    data = json.loads(text)
    assert data["version"] == VERSION == 1
    assert from_json(text) == panels


def test_unknown_fields_ignored():
    text = json.dumps(
        {
            "version": 1,
            "layout": "tabs",
            "panels": [
                {"id": ALL_ID, "name": "All", "filter": {}},
                {
                    "id": "p1",
                    "name": "Acme",
                    "color": "red",
                    "filter": {"clients": ["acme"], "owners": ["someone"]},
                },
            ],
        }
    )
    assert from_json(text) == [
        all_panel(),
        Panel(id="p1", name="Acme", filter=PanelFilter(clients=("acme",))),
    ]


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "not json {",
        "[]",
        "42",
        '"panels"',
        '{"version": 1}',
        '{"version": 1, "panels": "nope"}',
        '{"version": 1, "panels": []}',
    ],
)
def test_missing_or_corrupt_json_falls_back_to_all(text):
    # Decision: anything unreadable at the top level gives the default [All].
    # The app must keep starting; the user loses only custom panels.
    assert from_json(text) == default_panels()


def test_bad_panel_entries_are_skipped_and_good_ones_kept():
    # Decision: one malformed panel does not cost the user the others.
    text = json.dumps(
        {
            "version": 1,
            "panels": [
                {"id": ALL_ID, "name": "All", "filter": {}},
                "garbage",
                {"name": "no id", "filter": {}},
                {"id": "p2", "name": "bad filter", "filter": {"clients": "acme"}},
                {"id": "p3", "name": "bad value", "filter": {"tags": [1, 2]}},
                {"id": "p4", "name": "Good", "filter": {"tags": ["personal"]}},
                {"id": "p4", "name": "duplicate id", "filter": {}},
            ],
        }
    )
    assert from_json(text) == [
        all_panel(),
        Panel(id="p4", name="Good", filter=PanelFilter(tags=("personal",))),
    ]


def test_all_is_always_present_and_first():
    # Decision: "All" cannot be deleted and is always first, so a stored list
    # without it gets it prepended, and a stored one out of place moves first.
    missing = to_json([Panel(id="p1", name="Acme", filter=PanelFilter(clients=("acme",)))])
    assert [p.id for p in from_json(missing)] == [ALL_ID, "p1"]

    out_of_place = json.dumps(
        {
            "version": 1,
            "panels": [
                {"id": "p1", "name": "Acme", "filter": {"clients": ["acme"]}},
                {"id": ALL_ID, "name": "Everything", "filter": {}},
            ],
        }
    )
    panels = from_json(out_of_place)
    assert [p.id for p in panels] == [ALL_ID, "p1"]
    assert panels[0].name == "Everything"  # a renamed All keeps its name


def test_newer_version_is_read_best_effort():
    # A later release may add rule kinds; known fields still load.
    text = json.dumps(
        {
            "version": 99,
            "panels": [{"id": "p1", "name": "Prod", "filter": {"environments": ["prod"], "regex": "x"}}],
        }
    )
    assert from_json(text) == [
        all_panel(),
        Panel(id="p1", name="Prod", filter=PanelFilter(environments=("prod",))),
    ]
