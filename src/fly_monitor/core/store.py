"""On-disk state: sources, their secrets, and the last snapshot per source."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from .config import SourceConfig
from .health import Health
from .models import Item, Sample

SAMPLES_KEPT = 50  # per row; enough for any reasonable rule window

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id       TEXT PRIMARY KEY,
    plugin   TEXT NOT NULL,
    name     TEXT NOT NULL,
    interval INTEGER NOT NULL,
    tags     TEXT NOT NULL,
    settings TEXT NOT NULL,
    position INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS source_secrets (
    source_id TEXT NOT NULL,
    key       TEXT NOT NULL,
    value     TEXT NOT NULL,
    PRIMARY KEY (source_id, key)
);
CREATE TABLE IF NOT EXISTS source_state (
    source_id  TEXT PRIMARY KEY,
    last_sync  REAL,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS items (
    source_id  TEXT NOT NULL,
    item_id    TEXT NOT NULL,
    data       TEXT NOT NULL,
    first_seen REAL NOT NULL,
    PRIMARY KEY (source_id, item_id)
);
CREATE TABLE IF NOT EXISTS samples (
    source_id TEXT NOT NULL,
    key       TEXT NOT NULL,
    taken_at  REAL NOT NULL,
    ok        INTEGER NOT NULL,
    summary   TEXT NOT NULL,
    data      TEXT NOT NULL,
    url       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS samples_by_row ON samples (source_id, key, taken_at);
CREATE TABLE IF NOT EXISTS health (
    source_id TEXT NOT NULL,
    key       TEXT NOT NULL,
    data      TEXT NOT NULL,
    PRIMARY KEY (source_id, key)
);
CREATE TABLE IF NOT EXISTS ignored (
    source_id TEXT NOT NULL,
    item_id   TEXT NOT NULL,
    PRIMARY KEY (source_id, item_id)
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class SecretStore:
    """Where secret settings live. Plaintext in SQLite for now.

    This is the only class that touches secret values on disk, so moving to the
    OS keyring later means rewriting it and nothing else.
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def get_all(self, source_id: str) -> dict[str, str]:
        rows = self._db.execute("SELECT key, value FROM source_secrets WHERE source_id = ?", (source_id,))
        return dict(rows.fetchall())

    def put(self, source_id: str, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO source_secrets (source_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(source_id, key) DO UPDATE SET value = excluded.value",
            (source_id, key, value),
        )

    def delete_source(self, source_id: str) -> None:
        self._db.execute("DELETE FROM source_secrets WHERE source_id = ?", (source_id,))


class Store:
    def __init__(self, path: Path | str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        if path != ":memory:":
            os.chmod(path, 0o600)  # holds secrets: owner-only
        self._db.executescript(SCHEMA)
        self._migrate()
        self.secrets = SecretStore(self._db)

    def _migrate(self) -> None:
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(sources)")}
        if "rule" not in columns:  # databases from before sample-based sources
            self._db.execute("ALTER TABLE sources ADD COLUMN rule TEXT")

    # -- sources ----------------------------------------------------------------

    def list_sources(self) -> list[SourceConfig]:
        rows = self._db.execute(
            "SELECT id, plugin, name, interval, tags, settings, rule FROM sources ORDER BY position"
        ).fetchall()
        return [
            SourceConfig(
                id=source_id,
                plugin=plugin,
                name=name,
                interval=interval,
                tags=tuple(json.loads(tags)),
                settings={**json.loads(settings), **self.secrets.get_all(source_id)},
                rule=json.loads(rule) if rule else None,
            )
            for source_id, plugin, name, interval, tags, settings, rule in rows
        ]

    def add_source(self, source: SourceConfig, secret_keys: set[str]) -> None:
        public = {k: v for k, v in source.settings.items() if k not in secret_keys}
        with self._db:
            (position,) = self._db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM sources").fetchone()
            self._db.execute(
                "INSERT INTO sources (id, plugin, name, interval, tags, settings, position, rule) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source.id,
                    source.plugin,
                    source.name,
                    source.interval,
                    json.dumps(list(source.tags)),
                    json.dumps(public),
                    position,
                    json.dumps(source.rule) if source.rule else None,
                ),
            )
            for key in secret_keys & source.settings.keys():
                self.secrets.put(source.id, key, str(source.settings[key]))

    def remove_source(self, source_id: str) -> None:
        with self._db:
            for table, column in (
                ("sources", "id"),
                ("items", "source_id"),
                ("source_state", "source_id"),
                ("samples", "source_id"),
                ("health", "source_id"),
                ("ignored", "source_id"),
            ):
                self._db.execute(f"DELETE FROM {table} WHERE {column} = ?", (source_id,))
            self.secrets.delete_source(source_id)

    def get_meta(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # -- snapshots --------------------------------------------------------------

    def snapshot(self, source_id: str) -> dict[str, tuple[Item, float]]:
        rows = self._db.execute(
            "SELECT item_id, data, first_seen FROM items WHERE source_id = ?", (source_id,)
        )
        return {item_id: (Item.from_dict(json.loads(data)), first) for item_id, data, first in rows}

    def source_state(self, source_id: str) -> tuple[float | None, str | None]:
        row = self._db.execute(
            "SELECT last_sync, last_error FROM source_state WHERE source_id = ?", (source_id,)
        ).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def replace_snapshot(self, source_id: str, items: dict[str, tuple[Item, float]], synced_at: float) -> None:
        with self._db:
            self._db.execute("DELETE FROM items WHERE source_id = ?", (source_id,))
            self._db.executemany(
                "INSERT INTO items (source_id, item_id, data, first_seen) VALUES (?, ?, ?, ?)",
                [(source_id, i, json.dumps(item.to_dict()), first) for i, (item, first) in items.items()],
            )
            self._set_state(source_id, synced_at, None)

    # -- samples and health -----------------------------------------------------

    def health(self, source_id: str) -> dict[str, Health]:
        rows = self._db.execute("SELECT key, data FROM health WHERE source_id = ?", (source_id,))
        return {key: Health.from_dict(json.loads(data)) for key, data in rows}

    def latest_samples(self, source_id: str) -> dict[str, tuple[Sample, float]]:
        rows = self._db.execute(
            "SELECT key, ok, summary, data, url, MAX(taken_at) FROM samples WHERE source_id = ? GROUP BY key",
            (source_id,),
        )
        return {
            key: (Sample(key=key, ok=bool(ok), summary=summary, values=json.loads(data), url=url), taken_at)
            for key, ok, summary, data, url, taken_at in rows
        }

    def record_samples(self, source_id: str, samples: list[Sample], health: dict[str, Health], at: float) -> None:
        """Store this sync's samples and the resulting health. Rows no longer reported are dropped."""
        with self._db:
            self._db.executemany(
                "INSERT INTO samples (source_id, key, taken_at, ok, summary, data, url) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(source_id, s.key, at, int(s.ok), s.summary, json.dumps(s.values), s.url) for s in samples],
            )
            for sample in samples:
                self._db.execute(
                    "DELETE FROM samples WHERE source_id = ? AND key = ? AND taken_at NOT IN "
                    "(SELECT taken_at FROM samples WHERE source_id = ? AND key = ? ORDER BY taken_at DESC LIMIT ?)",
                    (source_id, sample.key, source_id, sample.key, SAMPLES_KEPT),
                )
            keys = [s.key for s in samples]
            marks = ",".join("?" * len(keys)) or "''"
            self._db.execute(f"DELETE FROM samples WHERE source_id = ? AND key NOT IN ({marks})", (source_id, *keys))
            self._db.execute("DELETE FROM health WHERE source_id = ?", (source_id,))
            self._db.executemany(
                "INSERT INTO health (source_id, key, data) VALUES (?, ?, ?)",
                [(source_id, key, json.dumps(h.to_dict())) for key, h in health.items()],
            )
            self._set_state(source_id, at, None)

    def set_health(self, source_id: str, key: str, health: Health) -> None:
        with self._db:
            self._db.execute(
                "UPDATE health SET data = ? WHERE source_id = ? AND key = ?",
                (json.dumps(health.to_dict()), source_id, key),
            )

    # -- ignored items ----------------------------------------------------------

    def ignored(self, source_id: str) -> set[str]:
        rows = self._db.execute("SELECT item_id FROM ignored WHERE source_id = ?", (source_id,))
        return {item_id for (item_id,) in rows}

    def ignore(self, source_id: str, item_id: str) -> None:
        with self._db:
            self._db.execute("INSERT OR IGNORE INTO ignored (source_id, item_id) VALUES (?, ?)", (source_id, item_id))

    def forget_ignored_except(self, source_id: str, still_present: set[str]) -> None:
        """An ignore lasts while the item is in the snapshot. Once it leaves, forget it."""
        with self._db:
            for item_id in self.ignored(source_id) - still_present:
                self._db.execute("DELETE FROM ignored WHERE source_id = ? AND item_id = ?", (source_id, item_id))

    def record_error(self, source_id: str, error: str) -> None:
        last_sync, _ = self.source_state(source_id)
        with self._db:
            self._set_state(source_id, last_sync, error)

    def _set_state(self, source_id: str, last_sync: float | None, error: str | None) -> None:
        self._db.execute(
            "INSERT INTO source_state (source_id, last_sync, last_error) VALUES (?, ?, ?) "
            "ON CONFLICT(source_id) DO UPDATE SET last_sync = excluded.last_sync, last_error = excluded.last_error",
            (source_id, last_sync, error),
        )

    def close(self) -> None:
        self._db.close()
