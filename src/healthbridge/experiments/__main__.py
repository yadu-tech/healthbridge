"""Command line: ``python -m healthbridge.experiments run`` / ``report``."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from healthbridge.db import connect, migrate
from healthbridge.experiments.report import render_report
from healthbridge.experiments.run import run_experiment
from healthbridge.staging.__main__ import latest_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m healthbridge.experiments")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the fault-injection experiment")
    run.add_argument("snapshot", type=Path, nargs="?", help="clean snapshot (default: latest in data/raw)")
    run.add_argument("--out", type=Path, default=Path("data/experiments/fault_injection"))
    run.add_argument("--seeds", type=int, default=5, help="number of seeds, 1..N (default 5)")
    run.add_argument("--passes", nargs="+", default=["validity", "magnitude", "schema"],
                     choices=["validity", "magnitude", "schema"])
    run.add_argument("--n-validity", type=int, default=200, help="faults per validity type per seed")
    run.add_argument("--n-magnitude", type=int, default=50, help="faults per magnitude level per seed")
    run.add_argument("--keep", action="store_true", help="keep the corrupted snapshots on disk")
    run.add_argument("--resume", action="store_true",
                     help="continue an interrupted run, keeping finished (pass, seed) results")

    rep = sub.add_parser("report", help="render results.json as Markdown")
    rep.add_argument("--results", type=Path, default=Path("data/experiments/fault_injection/results.json"))
    rep.add_argument("--out", type=Path, help="write to this file instead of stdout")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if args.command == "run":
        with connect() as conn:
            migrate(conn)
            snapshot = args.snapshot or latest_snapshot(Path("data/raw"))
            path = run_experiment(conn, snapshot, args.out, seeds=tuple(range(1, args.seeds + 1)),
                                  passes=tuple(args.passes), n_validity=args.n_validity,
                                  n_magnitude=args.n_magnitude, keep=args.keep, resume=args.resume)
        print(f"results: {path}")
        return 0

    text = render_report(json.loads(args.results.read_text(encoding="utf-8")),
                         faults_dir=args.results.parent / "faults")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
