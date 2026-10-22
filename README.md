# fly-monitor

Watch many sources from one terminal. Today: GitHub review requests, comments and mentions across several accounts, website uptime, and ActiveMQ Classic queues.

```sh
uv run fly-monitor         # a add · d delete · Enter open/fold · r sync · i ignore · k acknowledge · c close · q quit
uv run fly-monitor check   # test each source's connection
uv run pytest
```

Sources live in `~/.local/share/fly-monitor/state.db` (owner-only). An existing
`~/.config/fly-monitor/config.toml` is imported once on first run (see `config.example.toml`).

Secrets typed into the form go to your OS keyring (GNOME Keyring, KWallet, macOS Keychain,
Windows Credential Manager), never into that database. Without a keyring, for example over SSH,
use a `gh:<user>` or `env:VAR` reference instead, which stores no secret at all.

A website is failing when 5 of its last 10 checks failed, and recovers after 3 good checks in a row
(both adjustable when adding it). After a recovery only new checks count, so a flaky site doesn't flap.

An ActiveMQ queue has a bad check when one of its checks fires: no consumers, stuck (messages waiting
but none dequeued since the last sync) or a backlog. You can untick them and change the numbers when
adding the broker. By default a queue is failing after 3 bad checks in a row, synced every minute.

New GitHub items, and websites or queues going failing, raise a desktop popup (notify-send) and an in-app toast,
while fly-monitor is open. A source's first sync stays quiet.

Want to monitor something else? Write a plugin: see [PLUGINS.md](PLUGINS.md) and the
example in [`examples/fly-monitor-disk`](examples/fly-monitor-disk).

## License

MIT. See [LICENSE](LICENSE).
