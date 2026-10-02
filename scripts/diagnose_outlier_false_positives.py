"""Why does the temporal-outlier check raise alerts on rows the experiment never touched?

Replays one validity pass of the fault-injection experiment, then asks, for every untouched row
that was flagged, whether an injected row lies inside its +-2 neighbour window. It also measures
how often that is true for untouched rows in general, because "13 of 13" only means something
against that chance baseline.

Usage: python scripts/diagnose_outlier_false_positives.py [seed]
"""
from __future__ import annotations

import random
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from healthbridge.core.build import build_core
from healthbridge.db import connect
from healthbridge.experiments import evaluate as ev
from healthbridge.experiments import faults as fx
from healthbridge.experiments.run import purge_run
from healthbridge.staging.__main__ import latest_snapshot
from healthbridge.staging.load import load_snapshot


def main(seed: int = 1, n: int = 200) -> None:
    clean_dir = latest_snapshot(Path("data/raw"))
    with connect() as conn, tempfile.TemporaryDirectory() as tmp:
        store = fx.RawStore(clean_dir)
        pool = fx.build_pool(conn, store.manifest["run_id"])
        universe = fx.clean_universe(pool)
        parsed = store.working_copy()
        faults = fx.inject_validity(store, parsed, pool, random.Random(f"healthbridge-validity-{seed}"), n)
        run_id = f"fi_probe_{seed}"
        snapshot = fx.write_snapshot(store, parsed, run_id, Path(tmp))
        try:
            load_snapshot(conn, snapshot)
            build_core(conn, snapshot)
            outcomes = ev.read_outcomes(conn, run_id)
        finally:
            purge_run(conn, run_id)

    touched = {(f.file, f.row_num) for f in faults} | {(f.file, f.origin_row_num) for f in faults
                                                        if f.origin_row_num}
    injected = {(f.file, f.row_num): f.fault_type for f in faults}
    series = defaultdict(list)
    for row in sorted(pool, key=lambda r: (r.series, r.year)):
        series[row.series].append(row)

    def neighbour_faults(row: fx.PoolRow) -> list[str]:
        ordered = series[row.series]
        i = next(k for k, x in enumerate(ordered) if x.row_num == row.row_num)
        window = ordered[max(0, i - 2):i] + ordered[i + 1:i + 3]
        return [injected[(w.file, w.row_num)] for w in window if (w.file, w.row_num) in injected]

    untouched = [r for r in universe if (r.file, r.row_num) not in touched]
    flagged = [r for r in untouched
               if "temporal_outlier" in outcomes.facts.get((r.file, r.row_num), {"flags": []})["flags"]]
    with_neighbour = [r for r in flagged if neighbour_faults(r)]
    base_rate = sum(bool(neighbour_faults(r)) for r in untouched) / len(untouched)
    kinds = Counter(k for r in with_neighbour for k in set(neighbour_faults(r)))
    print(f"seed {seed}: {len(faults)} faults injected, {len(untouched):,} untouched clean rows")
    print(f"untouched rows flagged as temporal outliers: {len(flagged)}")
    print(f"  of those, with an injected row inside their +-2 window: {len(with_neighbour)}")
    print(f"  chance baseline (untouched rows with an injected neighbour): {base_rate:.1%}")
    print(f"  fault types of those neighbours: {dict(kinds)}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
