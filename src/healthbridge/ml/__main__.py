"""Command line: ``python -m healthbridge.ml gate [--out FILE]``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from healthbridge.db import connect, migrate
from healthbridge.ml.dataset import build_instances, load_panel
from healthbridge.ml.gate import render_gate_report, run_gate
from healthbridge.ml.stage2 import run_stage2
from healthbridge.reference import indicator_ranges


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m healthbridge.ml")
    sub = parser.add_subparsers(dest="command", required=True)
    gate = sub.add_parser("gate", help="evaluate whether the data supports a forecasting task")
    gate.add_argument("--out", type=Path, help="write the report to this file instead of stdout")
    models = sub.add_parser("models", help="compare the pooled models with the baselines (stage 2)")
    models.add_argument("--out", type=Path, help="write the report to this file instead of stdout")
    args = parser.parse_args(argv)

    with connect() as conn:
        migrate(conn)
        if args.command == "models":
            panel = load_panel(conn)
            ranges = indicator_ranges()
            text = run_stage2(build_instances(panel, ranges), ranges)["report"]
        else:
            text = render_gate_report(run_gate(conn))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
