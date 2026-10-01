"""Run the fault-injection experiment end to end against the real pipeline.

For every seed and pass: corrupt a copy of the clean snapshot, load it into staging, build the
core layer, read what the pipeline did with each injected fault, then remove the experiment's
rows from the database. Results are written after every run so a long experiment can resume
reading from partial output.
"""
from __future__ import annotations

import json
import logging
import platform
import random
import shutil
import subprocess
import time
from pathlib import Path

import psycopg

from healthbridge.core.build import build_core
from healthbridge.experiments import evaluate as ev
from healthbridge.experiments import faults as fx
from healthbridge.ingest.snapshot import MANIFEST_NAME
from healthbridge.staging.load import load_snapshot, schema_reports

log = logging.getLogger(__name__)

PURGE_ORDER = (
    "core.fact_reconciled", "core.evidence_group", "core.source_dependence", "core.fact_observation",
    "core.rejected_record", "core.build_log", "staging.who_observation",
    "staging.worldbank_observation", "staging.unicef_observation", "staging.load_log",
)


def purge_run(conn: psycopg.Connection, run_id: str) -> None:
    with conn.transaction():
        for table in PURGE_ORDER:
            conn.execute(f"DELETE FROM {table} WHERE run_id = %s", (run_id,))


def _git_version() -> str:
    """Commit the experiment ran against (with a marker if the working tree had changes)."""
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True,
                               check=True).stdout.strip()
        return sha + ("+uncommitted" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _schema_result(fault: fx.Fault, labels: list[str]) -> dict:
    return {"fault_type": fault.fault_type, "variant": fault.variant, "source": fault.source,
            "concept": "", "labels": labels, "expected": fault.expected,
            "correct": bool(set(labels) & set(fault.expected)), "silent": labels == ["silent"],
            "naive": None, "n_neighbors": 0, "cell_members": 0, "key": (fault.file, 0, "", "", 0)}


def _faulty_bytes(snapshot_dir: Path, faults: list[fx.Fault]) -> dict[str, bytes]:
    return {path: (snapshot_dir / path).read_bytes() for path in {f.file for f in faults}}


def run_pass(conn, store: fx.RawStore, pool, universe, out_dir: Path, name: str, seed: int,
             n_validity: int, n_magnitude: int, keep: bool) -> dict:
    started = time.perf_counter()
    rng = random.Random(f"healthbridge-{name}-{seed}")
    parsed = store.working_copy()
    run_id = f"fi_{name}_s{seed}"
    if name == "validity":
        faults = fx.inject_validity(store, parsed, pool, rng, n_validity)
    elif name == "magnitude":
        faults = fx.inject_magnitude(store, parsed, pool, rng, n_magnitude)
    else:
        faults = fx.inject_schema(store, parsed, rng)
    snapshot_dir = fx.write_snapshot(store, parsed, run_id, out_dir / "snapshots")
    (out_dir / "faults").mkdir(parents=True, exist_ok=True)
    fx.write_log(faults, out_dir / "faults" / f"{run_id}.jsonl")

    sources = {path: e["source"] for path, e in store.entries.items()}
    if name == "schema":
        manifest = json.loads((snapshot_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
        reports = schema_reports(snapshot_dir, manifest)
        results = [_schema_result(f, ev.classify_schema(f, reports)) for f in faults]
        record = {"pass": name, "seed": seed, "run_id": run_id, "n_faults": len(faults),
                  "results": results, "false_positives": {}}
    else:
        try:
            load_snapshot(conn, snapshot_dir)
            build_core(conn, snapshot_dir)
            outcomes = ev.read_outcomes(conn, run_id)
            results = ev.evaluate_run(faults, outcomes, _faulty_bytes(snapshot_dir, faults), sources)
            fps = ev.false_positives(universe, outcomes, faults)
        finally:
            purge_run(conn, run_id)
        record = {"pass": name, "seed": seed, "run_id": run_id, "n_faults": len(faults),
                  "results": results, "false_positives": fps}
    if not keep:
        shutil.rmtree(snapshot_dir)
    record["seconds"] = round(time.perf_counter() - started, 1)
    log.info("%s seed %d: %d faults in %.0fs", name, seed, len(faults), record["seconds"])
    return record


def run_experiment(conn: psycopg.Connection, clean_run_dir: Path, out_dir: Path,
                   seeds: tuple[int, ...] = (1, 2, 3, 4, 5), passes: tuple[str, ...] = (
                       "validity", "magnitude", "schema"),
                   n_validity: int = 200, n_magnitude: int = 50, keep: bool = False) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    store = fx.RawStore(clean_run_dir)
    clean_run_id = store.manifest["run_id"]
    pool = fx.build_pool(conn, clean_run_id)
    universe = fx.clean_universe(pool)
    summary = {
        "code_version": _git_version(), "python": platform.python_version(),
        "clean_run_id": clean_run_id, "seeds": list(seeds), "passes": list(passes),
        "n_validity_per_type": n_validity, "n_magnitude_per_level": n_magnitude,
        "pool_rows": len(pool), "universe_rows": len(universe),
        "assessable_by_outlier_check": sum(p.n_neighbors >= 3 for p in pool),
        "clean_alerts": ev.clean_alert_rates(conn, clean_run_id),
        "runs": [],
    }
    results_path = out_dir / "results.json"
    for seed in seeds:
        for name in passes:
            summary["runs"].append(run_pass(conn, store, pool, universe, out_dir, name, seed,
                                            n_validity, n_magnitude, keep))
            results_path.write_text(json.dumps(summary, indent=1, default=list), encoding="utf-8")
    return results_path
