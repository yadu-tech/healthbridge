"""Command line: ``python -m healthbridge.dq baseline [snapshot]`` / ``report [--dq-run N] [--out FILE]``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from healthbridge.db import connect, migrate
from healthbridge.dq.baseline import compute_baseline
from healthbridge.dq.report import render_report
from healthbridge.staging.__main__ import latest_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m healthbridge.dq")
    sub = parser.add_subparsers(dest="command", required=True)
    base = sub.add_parser("baseline", help="measure the staged snapshot and store the metrics")
    base.add_argument("snapshot", type=Path, nargs="?", help="snapshot dir (default: latest in data/raw)")
    rep = sub.add_parser("report", help="render a stored run as Markdown")
    rep.add_argument("--dq-run", type=int, help="dq run id (default: latest)")
    rep.add_argument("--out", type=Path, help="write to this file instead of stdout")
    args = parser.parse_args(argv)

    with connect() as conn:
        migrate(conn)
        if args.command == "baseline":
            snapshot = args.snapshot or latest_snapshot(Path("data/raw"))
            print(f"dq run {compute_baseline(conn, snapshot)} stored for snapshot {snapshot.name}")
            return 0
        run_id = args.dq_run or conn.execute("SELECT max(dq_run_id) FROM dq.run").fetchone()[0]
        if run_id is None:
            raise SystemExit("no data-quality runs yet; run `python -m healthbridge.dq baseline`")
        text = render_report(conn, run_id)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
            print(f"wrote {args.out}")
        else:
            print(text)
        return 0


if __name__ == "__main__":
    sys.exit(main())
