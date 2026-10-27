# Changelog

Changes to the plugin contract are marked **Plugins**.

## 0.1.0 (unreleased)

- Terminal UI with sources grouped and collapsible.
- GitHub source: review requests, comments and mentions, accounts detected through `gh`.
- Website source: status code and response time, failing after 5 of the last 10 checks.
- Desktop and in-app notifications, acknowledge and close, ignore.
- Secrets live in the OS keyring. Older databases are migrated on startup.
- ActiveMQ Classic source: one row per queue (waiting, consumers, and messages in/out since the last check), queues picked from the broker or by pattern.
- Checks: rules on a source's numbers, edited in the add-source form.
- Attention first: source headers count failing/acknowledged/ok rows and turn red or orange; failing rows sort first.
- Recovered notification when a failing row is healthy again.
- Overview grouped by client › kind (`g` cycles: kind, environment › kind, flat); `-`/`+` fold or unfold one level at a time. Sources get client and environment fields.
- Each kind gets its own columns (queues: waiting, consumers, in, out); healthy sources take one line.
- Panels: tabs that each show a saved filter (client, environment, tag, type, plus hand-picked sources). `p` manages them, `P` turns a group header into one, `1`–`9` and `Tab` switch, and each tab shows its failing count. "All" is always there. Plugins need no change: any source can be filtered by type, client or tag.
- Notifications tab: this session's events, newest first; Enter opens one.
- **Plugins:** optional `title`; declared metrics become the columns of your rows.
- Edit a source in place (`e`): everything but its identity (URL, account). History is kept.
- **Plugins:** `Metric`, `Check`/`Condition` and `default_checks` for samples plugins; `Setting.fetch` with the `options(ctx, key)` hook.
- **Plugins:** first version of the contract, see [PLUGINS.md](PLUGINS.md).
