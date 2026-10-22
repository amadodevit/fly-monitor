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
- Edit a source in place (`e`): everything but its identity (URL, account). History is kept.
- **Plugins:** `Metric`, `Check`/`Condition` and `default_checks` for samples plugins; `Setting.fetch` with the `options(ctx, key)` hook.
- **Plugins:** first version of the contract, see [PLUGINS.md](PLUGINS.md).
