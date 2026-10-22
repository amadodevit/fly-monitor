# Writing a fly-monitor plugin

A plugin is a Python class that says what it needs and reports what is true right now.
The core does everything else: scheduling, storage, history, health, notifications and the UI.

The quickest start is to copy [`examples/fly-monitor-disk`](examples/fly-monitor-disk) and rename it.

> **Stability.** fly-monitor is 0.x. The contract may change between minor versions.
> Every change is listed in [CHANGELOG.md](CHANGELOG.md) under **Plugins**.

## A minimal plugin

```python
from fly_monitor.core.models import Item
from fly_monitor.core.plugin import Setting, SyncContext


class TodoSource:
    name = "todo"              # unique; shown in the add-source wizard
    connection = "http"        # the connection the core gives you as ctx.http
    settings = (Setting("url", "Todo list URL"),)

    def sync(self, ctx: SyncContext) -> list[Item]:
        todos = ctx.http.get_json(ctx.settings["url"])
        return [
            Item(id=str(t["id"]), action="open", title=t["title"], url=t["link"],
                 repo="", author=t["owner"], updated_at=t["updated"])
            for t in todos
        ]
```

Register it in your package's `pyproject.toml`:

```toml
[project.entry-points."fly_monitor.sources"]
todo = "fly_monitor_todo:TodoSource"
```

Install your package next to fly-monitor and it appears under `a` (add source).

## Settings become the form

You never write UI. Each `Setting` becomes one field in the add-source wizard.

| Field | Effect |
|---|---|
| `key`, `label` | The key in `ctx.settings` and the label in the form. |
| `required` | The form refuses to add the source while it is empty. Default `True`. |
| `secret` | Masked in the form and kept in the OS keyring, never in the database. |
| `default` | Prefilled value, also used when the user leaves it empty. |
| `help` | One line under the field. Put an example here. |
| `choices` | Turns the field into a checklist. The value is a list of the ticked `Choice.value`s. |
| `fetch` | Adds a Fetch button. The form calls your `options(ctx, key)` and lets the user tick values (e.g. queue names). |

Values arrive as strings (or a list, for choices). Convert them in `sync`.

## Items or samples

Pick the kind that matches what you watch. Set `kind = "samples"` for the second one; items is the default.

**Items** are things that need attention: a review request, an open alert.
Return the full list each sync. The core compares it with the last one.
New ids raise a notification, missing ids are resolved, and the user can ignore a row.
Make `id` stable: the same thing must get the same id on every sync.

**Samples** are measurements: a status code, disk usage, a queue length.
Return one `Sample(key, ok, summary, values, url)` per row each sync.
You only say whether this one check passed. The core judges it over time with a rule
(by default 5 failed of the last 10, then 3 good in a row to recover), and handles failing,
acknowledge and close.

### Metrics and checks

If your samples carry numbers, declare them and let users write rules on them.
You report the numbers; the core decides when they are bad.

```python
from fly_monitor.core.checks import Check, Condition
from fly_monitor.core.plugin import Metric

metrics = (
    Metric("size", "Messages waiting"),
    Metric("dequeued", "Dequeued", counter=True, short="out"),   # only goes up
)
default_checks = (
    Check("stuck", "Stuck", (Condition("size", ">", 0), Condition("dequeued", "=", 0))),
)
```

- A check fires when all its conditions hold. Any firing check makes that sample a bad check.
- A `counter=True` metric is compared by its growth since the previous sync, not its raw value.
  On the first sync, or after a reset (the number went down), counter conditions don't fire.
- The core also adds each counter's growth to the row, e.g. `+1.2k out`, using `short` (or the label).
- Your first four metrics are the columns of your rows in the overview, headed by `short` (or the label).
- Users see your default checks in the add-source form, can untick them and change the numbers.
  They double as examples, so make them sensible.

See `src/fly_monitor/plugins/activemq.py` for a complete example.

A failed check is a sample with `ok=False`, not an exception. Raise only when the plugin
itself cannot run (bad token, unknown host). The core then shows the error on the source.

## Optional hooks

Add these when they help. The core checks for them with `hasattr`.

| Member | What it does |
|---|---|
| `test_connection(ctx) -> str` | Powers the Test button and `fly-monitor check`. Return a short description, raise on failure. |
| `detect() -> list[Candidate]` | Finds sources on this machine (e.g. a logged-in CLI). The wizard shows them first. Return `[]` when nothing is found. |
| `identity` | Setting keys that identify a source, e.g. `("account",)`. Marks detected candidates that are already added. |
| `title` | Name of your kind in the overview, e.g. "ActiveMQ queues". Default: `name`. |
| `default_interval` | Seconds between syncs. Default one hour. |
| `default_rule` | A `Rule(window, fail_after, recover_after)` for samples. Users can change it when adding. |
| `metrics`, `default_checks` | Samples only: the numbers you report and the editable checks on them. See above. |
| `options(ctx, key) -> list[str]` | Values to pick from for a `Setting` with `fetch=True`. |

## Connections and secrets

`connection` names a client the core provides. Today there is one, `"http"`
(`ctx.http.get_json(url, headers=..., params=...)` and `ctx.http.probe(url, timeout=...)`).
A plugin that needs no network still names `"http"` and ignores it.
If you need another kind, open an issue: the core should own it, not each plugin.

Secret settings are resolved before `sync`, so `ctx.settings["token"]` is always the real value.
Users can type a reference instead of the secret:

- `gh:<user>` reads the token from the GitHub CLI.
- `env:VAR` reads an environment variable.

Never log or return a secret in a summary or error message.

## Testing

`sync` takes plain data and returns plain data, so tests need no network.
Pass a fake in place of `ctx.http`:

```python
class FakeHttp:
    def get_json(self, url, **kwargs):
        return [{"id": 1, "title": "Ship it", "link": "https://example.com/1",
                 "owner": "alice", "updated": "2026-01-01T00:00:00Z"}]


def test_sync():
    items = TodoSource().sync(SyncContext(settings={"url": "https://example.com"}, http=FakeHttp()))
    assert [i.title for i in items] == ["Ship it"]
```

Also check `isinstance(TodoSource(), SourcePlugin)`; it catches a missing required member.
See `tests/test_website.py` and `tests/test_example_plugin.py` for more.

## Publishing

Name the package `fly-monitor-<name>` so people can find it, and say which fly-monitor
versions you tested with. Want it built in? Open an issue first, so we can agree on scope.
