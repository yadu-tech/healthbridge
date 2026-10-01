"""Command line for the data-quality layer.

    python -m healthbridge.dq baseline [snapshot] [--stage staging|core]
    python -m healthbridge.dq report [--dq-run N] [--out FILE]
    python -m healthbridge.dq compare [--before N] [--after M] [--out FILE]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from healthbridge.db import connect, migrate
from healthbridge.dq.baseline import compute_baseline
from healthbridge.dq.compare import render_comparison
from healthbridge.dq.report import render_report
from healthbridge.staging.__main__ import latest_snapshot


def _emit(text: str, out: Path | None) -> None:
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m healthbridge.dq")
    sub = parser.add_subparsers(dest="command", required=True)

    base = sub.add_parser("baseline", help="measure a layer for a snapshot and store the metrics")
    base.add_argument("snapshot", type=Path, nargs="?", help="snapshot dir (default: latest in data/raw)")
    base.add_argument("--stage", choices=["staging", "core"], default="staging",
                      help="which layer to measure (default: staging)")

    rep = sub.add_parser("report", help="render a stored run as Markdown")
    rep.add_argument("--dq-run", type=int, help="dq run id (default: latest)")
    rep.add_argument("--out", type=Path, help="write to this file instead of stdout")

    cmp_ = sub.add_parser("compare", help="before (staging) vs after (core) for one snapshot")
    cmp_.add_argument("--before", type=int, help="dq run id of the staging run (default: latest)")
    cmp_.add_argument("--after", type=int, help="dq run id of the core run (default: latest)")
    cmp_.add_argument("--out", type=Path, help="write to this file instead of stdout")

    args = parser.parse_args(argv)
    with connect() as conn:
        migrate(conn)
        if args.command == "baseline":
            snapshot = args.snapshot or latest_snapshot(Path("data/raw"))
            run = compute_baseline(conn, snapshot, stage=args.stage)
            print(f"dq run {run} ({args.stage}) stored for snapshot {snapshot.name}")
            return 0

        def latest(stage: str | None = None) -> int | None:
            sql = "SELECT max(dq_run_id) FROM dq.run" + (" WHERE stage = %s" if stage else "")
            return conn.execute(sql, (stage,) if stage else ()).fetchone()[0]

        if args.command == "report":
            run_id = args.dq_run or latest()
            if run_id is None:
                raise SystemExit("no data-quality runs yet; run `python -m healthbridge.dq baseline`")
            _emit(render_report(conn, run_id), args.out)
            return 0

        before, after = args.before or latest("staging"), args.after or latest("core")
        if before is None or after is None:
            raise SystemExit("need a staging run and a core run; run `baseline` for both stages")
        _emit(render_comparison(conn, before, after), args.out)
        return 0


if __name__ == "__main__":
    sys.exit(main())
