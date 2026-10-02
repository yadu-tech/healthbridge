"""Build the analytics marts for one snapshot from the core layer.

Everything for a snapshot is replaced inside one transaction, so a rebuild is idempotent and a
failure leaves the previous marts untouched. The statistical choices (trend window, reference
years, tolerance, minimum country counts) are parameters recorded in ``marts.build_log``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import psycopg

from healthbridge.db import REPO_ROOT
from healthbridge.ingest.snapshot import MANIFEST_NAME

MARTS_DIR = REPO_ROOT / "sql" / "marts"

MART_TABLES = (
    "country_indicator_year", "country_latest", "trend", "region_year",
    "indicator_association", "equity_gap", "data_trust",
)
STEPS = ("10_panel.sql", "20_latest.sql", "30_trend.sql", "40_region.sql",
         "50_association_level.sql", "51_association_change.sql", "60_equity.sql", "70_trust.sql")

# Analytical judgements, recorded with every build.
DEFAULTS = {
    "trend_start": 2000,        # window for trends
    "trend_min_points": 4,      # a slope needs at least this many observations ...
    "trend_min_span": 8,        # ... spread over at least this many years
    "ref_year": 2015,           # reference year for cross-country comparisons
    "base_year": 2000,          # start year for change comparisons
    "tolerance": 3,             # use the observation nearest a reference year within +-3 years
    "change_min_span": 8,       # a change needs its two ends at least this far apart
    "min_countries": 10,        # an association needs at least this many countries
}


def _sql(name: str) -> str:
    return (MARTS_DIR / name).read_text(encoding="utf-8")


def build_marts(conn: psycopg.Connection, run_dir: Path, **overrides) -> dict:
    started = time.perf_counter()
    params = {**DEFAULTS, **overrides}
    manifest = json.loads((Path(run_dir) / MANIFEST_NAME).read_text(encoding="utf-8"))
    run_id = manifest["run_id"]
    year_min, year_max = manifest["scope"]["years"]
    params.update(
        run_id=run_id, year_min=year_min, year_max=year_max,
        level_label=f"{params['ref_year']} +-{params['tolerance']}y",
        change_label=f"{params['base_year']} to {params['ref_year']} +-{params['tolerance']}y",
    )

    with conn.transaction():
        if not conn.execute("SELECT 1 FROM core.build_log WHERE run_id = %s", (run_id,)).fetchone():
            raise RuntimeError(f"snapshot {run_id} has no core layer; run `python -m healthbridge.core build`")
        params["n_countries"] = conn.execute("SELECT count(*) FROM core.dim_country").fetchone()[0]
        for table in MART_TABLES:
            conn.execute(f"DELETE FROM marts.{table} WHERE run_id = %s", (run_id,))
        conn.execute("DELETE FROM marts.build_log WHERE run_id = %s", (run_id,))
        for step in STEPS:
            conn.execute(_sql(step), params)
        rows = {t: conn.execute(f"SELECT count(*) FROM marts.{t} WHERE run_id = %s",
                                (run_id,)).fetchone()[0] for t in MART_TABLES}
        logged = {k: v for k, v in params.items()
                  if k in DEFAULTS or k in ("year_min", "year_max", "n_countries")}
        conn.execute(
            "INSERT INTO marts.build_log (run_id, rows, parameters, duration_seconds)"
            " VALUES (%s, %s, %s, %s)",
            (run_id, json.dumps(rows), json.dumps(logged), time.perf_counter() - started),
        )
    return {"run_id": run_id, **rows}
