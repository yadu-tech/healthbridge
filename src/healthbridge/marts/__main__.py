"""Command line: ``python -m healthbridge.marts build [snapshot_dir]`` / ``report [--out FILE]``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from healthbridge.db import connect, migrate
from healthbridge.marts.build import build_marts
from healthbridge.marts.report import render_marts_report
from healthbridge.staging.__main__ import latest_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m healthbridge.marts")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build the analytics marts from the core layer")
    build.add_argument("snapshot", type=Path, nargs="?", help="snapshot dir (default: latest in data/raw)")
    report = sub.add_parser("report", help="render an analytics report from the latest marts")
    report.add_argument("--out", type=Path, help="write to this file instead of stdout")
    args = parser.parse_args(argv)

    with connect() as conn:
        migrate(conn)
        if args.command == "build":
            snapshot = args.snapshot or latest_snapshot(Path("data/raw"))
            summary = build_marts(conn, snapshot)
            print(f"marts built for {summary['run_id']}: " + ", ".join(
                f"{k}={v:,}" for k, v in summary.items() if k != "run_id"))
            return 0
        text = render_marts_report(conn)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
            print(f"wrote {args.out}")
        else:
            print(text)
        return 0


if __name__ == "__main__":
    sys.exit(main())
