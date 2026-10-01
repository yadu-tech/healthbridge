"""Command line: ``python -m healthbridge.staging migrate`` / ``load [snapshot_dir]``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from healthbridge.db import connect, migrate
from healthbridge.staging.load import load_snapshot


def latest_snapshot(root: Path) -> Path:
    runs = sorted(p for p in root.glob("*") if (p / "manifest.json").exists())
    if not runs:
        raise SystemExit(f"no snapshots found under {root}; run `python -m healthbridge.ingest run`")
    return runs[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m healthbridge.staging")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply pending database migrations")
    load = sub.add_parser("load", help="load a verified raw snapshot into staging")
    load.add_argument("snapshot", type=Path, nargs="?", help="snapshot dir (default: latest in data/raw)")
    args = parser.parse_args(argv)

    with connect() as conn:
        if args.command == "migrate":
            applied = migrate(conn)
            print("applied: " + (", ".join(applied) if applied else "nothing (up to date)"))
            return 0
        migrate(conn)
        snapshot = args.snapshot or latest_snapshot(Path("data/raw"))
        counts = load_snapshot(conn, snapshot)
        print(f"loaded {snapshot.name}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        return 0


if __name__ == "__main__":
    sys.exit(main())
