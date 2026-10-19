# fly-monitor

Watch many sources from one terminal. Today: GitHub review requests, comments and mentions across several accounts, and website uptime.

```sh
uv run fly-monitor         # a add · d delete · Enter open/fold · r sync · i ignore · k acknowledge · c close · q quit
uv run fly-monitor check   # test each source's connection
uv run pytest
```

Sources live in `~/.local/share/fly-monitor/state.db` (owner-only). An existing
`~/.config/fly-monitor/config.toml` is imported once on first run (see `config.example.toml`).

Secrets typed into the form are stored in plaintext in that database for now. Prefer
`gh:<user>` or `env:VAR` references, which store no secret at all.

A website is failing when 5 of its last 10 checks failed, and recovers after 3 good checks in a row
(both adjustable when adding it). After a recovery only new checks count, so a flaky site doesn't flap.

New GitHub items and websites going failing raise a desktop popup (notify-send) and an in-app toast,
while fly-monitor is open. A source's first sync stays quiet.

Plugins register through the `fly_monitor.sources` entry point group. Their declared
settings become the add-source form. See `src/fly_monitor/core/plugin.py` for the contract.
