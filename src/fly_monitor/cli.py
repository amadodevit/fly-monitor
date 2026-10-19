from __future__ import annotations

import argparse
import sys

from fly_monitor.core.config import config_path, data_path, load_config
from fly_monitor.core.engine import Core
from fly_monitor.core.registry import discover
from fly_monitor.core.store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fly-monitor")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check", help="test each source's connection and exit")
    args = parser.parse_args(argv)

    core = Core(discover(), Store(data_path()))
    notice = _import_config_once(core)
    if notice is None:
        return 1

    if args.command == "check":
        return _check(core)

    from fly_monitor.ui.app import FlyMonitorApp  # keep `check` fast and UI-free

    FlyMonitorApp(core, notice=notice).run()
    return 0


def _import_config_once(core: Core) -> str | None:
    """Returns a notice for the user ("" if nothing happened), or None on a fatal error."""
    path = config_path()
    if not path.exists():
        return ""
    try:
        count = core.import_config(load_config(path))
    except ValueError as exc:
        print(f"Could not import {path}: {exc}", file=sys.stderr)
        return None
    if not count:
        return ""
    return f"Imported {count} source{'s' * (count != 1)} from {path}. That file is no longer read."


def _check(core: Core) -> int:
    ids = core.source_ids()
    if not ids:
        print("No sources yet. Run fly-monitor and press a to add one.")
        return 0
    failed = 0
    for source_id in ids:
        try:
            print(f"✓ {source_id}: {core.test_connection(source_id)}")
        except Exception as exc:
            failed += 1
            print(f"✗ {source_id}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
