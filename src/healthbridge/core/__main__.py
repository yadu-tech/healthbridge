"""Command line: ``python -m healthbridge.core build [snapshot_dir]``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from healthbridge.core.build import build_core
from healthbridge.db import connect, migrate
from healthbridge.staging.__main__ import latest_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m healthbridge.core")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build the core layer from a loaded staging snapshot")
    build.add_argument("snapshot", type=Path, nargs="?", help="snapshot dir (default: latest in data/raw)")
    args = parser.parse_args(argv)

    with connect() as conn:
        migrate(conn)
        snapshot = args.snapshot or latest_snapshot(Path("data/raw"))
        summary = build_core(conn, snapshot)
        print(f"core built for {summary['run_id']}: " + ", ".join(
            f"{k}={v}" for k, v in summary.items() if k != "run_id"))
        return 0


if __name__ == "__main__":
    sys.exit(main())
