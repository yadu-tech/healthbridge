"""Command line: ``python -m healthbridge.ingest run`` / ``verify <snapshot_dir>``."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from healthbridge.ingest.runner import run_ingestion
from healthbridge.ingest.snapshot import verify_snapshot
from healthbridge.ingest.sources import SOURCES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m healthbridge.ingest")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="fetch sources into a new immutable snapshot")
    run.add_argument("--out", type=Path, default=Path("data/raw"), help="snapshot root directory")
    run.add_argument("--sources", nargs="+", choices=sorted(SOURCES), default=sorted(SOURCES))

    verify = sub.add_parser("verify", help="recompute checksums for a snapshot directory")
    verify.add_argument("snapshot", type=Path)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.command == "run":
        writer = run_ingestion(args.out, sources=args.sources)
        print(f"snapshot: {writer.run_dir}")
        print(f"files: {len(writer.files)}  failures: {len(writer.failures)}")
        for failure in writer.failures:
            print(f"  FAILED {failure['source']}/{failure['concept']}: {failure['error']}")
        return 1 if writer.failures else 0

    problems = verify_snapshot(args.snapshot)
    if problems:
        print("snapshot is NOT intact:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("snapshot OK: all checksums match the manifest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
