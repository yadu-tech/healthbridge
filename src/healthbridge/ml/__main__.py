"""Command line for the machine-learning component.

    python -m healthbridge.ml gate [--out FILE]
    python -m healthbridge.ml models [--out FILE]
    python -m healthbridge.ml ablation run [snapshot] [--dir DIR] [--no-resume]
    python -m healthbridge.ml ablation report [--dir DIR] [--out FILE]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from healthbridge.db import connect, migrate
from healthbridge.ml.ablation import build_summary, render_ablation_report, run_ablation
from healthbridge.ml.dataset import build_instances, load_panel
from healthbridge.ml.gate import render_gate_report, run_gate
from healthbridge.ml.stage2 import run_stage2
from healthbridge.reference import indicator_ranges
from healthbridge.staging.__main__ import latest_snapshot

DEFAULT_DIR = Path("data/experiments/ablation")


def _emit(text: str, out: Path | None) -> None:
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m healthbridge.ml")
    sub = parser.add_subparsers(dest="command", required=True)
    gate = sub.add_parser("gate", help="evaluate whether the data supports a forecasting task")
    gate.add_argument("--out", type=Path, help="write the report to this file instead of stdout")
    models = sub.add_parser("models", help="compare the pooled models with the baselines (stage 2)")
    models.add_argument("--out", type=Path, help="write the report to this file instead of stdout")
    ablation = sub.add_parser("ablation", help="the data-quality ablation")
    abl = ablation.add_subparsers(dest="action", required=True)
    run = abl.add_parser("run", help="corrupt the data, process it three ways, score the forecasts")
    run.add_argument("snapshot", type=Path, nargs="?", help="clean snapshot (default: latest in data/raw)")
    run.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    run.add_argument("--no-resume", action="store_true", help="recompute even finished corruptions")
    rep = abl.add_parser("report", help="render the ablation report")
    rep.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    rep.add_argument("--out", type=Path, help="write the report to this file instead of stdout")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if args.command == "ablation" and args.action == "report":
        _emit(render_ablation_report(build_summary(args.dir)), args.out)
        return 0
    with connect() as conn:
        migrate(conn)
        if args.command == "ablation":
            snapshot = args.snapshot or latest_snapshot(Path("data/raw"))
            path = run_ablation(conn, snapshot, args.dir, resume=not args.no_resume)
            print(f"ablation results: {path}")
            return 0
        if args.command == "models":
            panel = load_panel(conn)
            ranges = indicator_ranges()
            text = run_stage2(build_instances(panel, ranges), ranges)["report"]
        else:
            text = render_gate_report(run_gate(conn))
    _emit(text, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
