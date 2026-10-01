"""Build the core layer from a loaded staging snapshot.

Order: reference data -> consistency checks against the staged data -> classify and load
rows (SQL in ``sql/transform``) -> measure source dependence -> reconcile. Everything for a
snapshot is replaced inside one transaction, so a rebuild is idempotent and a failure leaves
the previous state untouched.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import psycopg

from healthbridge.db import REPO_ROOT
from healthbridge.ingest.snapshot import MANIFEST_NAME
from healthbridge.reference import ReferenceMismatch, _add_alias, load_reference

TRANSFORM_DIR = REPO_ROOT / "sql" / "transform"

# Tunable judgements, recorded in core.build_log so every result states the values it used.
DEPENDENCE_TOLERANCE = 0.01   # two values "agree" if within 1% of the larger
DEPENDENCE_MIN_SHARED = 30    # fewer shared country-years than this is too little evidence
DEPENDENCE_MIN_SHARE = 0.90   # share of shared cells that must agree to call sources dependent
CONFLICT_TOLERANCE = 0.10     # independent evidence groups differing by more than 10% = conflict


def _sql(name: str) -> str:
    return (TRANSFORM_DIR / name).read_text(encoding="utf-8")


def _check_against_staging(conn: psycopg.Connection, run_id: str) -> None:
    """Reference data must agree with what the sources actually say about countries."""
    reference = dict(conn.execute("SELECT iso3, iso2 FROM core.dim_country").fetchall())
    reference = {k.strip(): v.strip() for k, v in reference.items()}
    problems = []
    for iso3, iso2 in conn.execute(
        "SELECT DISTINCT country_iso3, country_id FROM staging.worldbank_observation"
        " WHERE run_id = %s", (run_id,)
    ).fetchall():
        if iso3 not in reference:
            problems.append(f"World Bank country {iso3} is not in reference/countries.csv")
        elif reference[iso3] != iso2:
            problems.append(f"ISO2 mismatch for {iso3}: reference {reference[iso3]}, World Bank {iso2}")
    if problems:
        raise ReferenceMismatch("; ".join(problems))

    for name, iso3 in conn.execute(
        "SELECT DISTINCT country_name, country_iso3 FROM staging.worldbank_observation"
        " WHERE run_id = %s AND country_name IS NOT NULL", (run_id,)
    ).fetchall():
        _add_alias(conn, name, iso3, "world_bank_name")

    for iso3, codes, names in conn.execute(
        "SELECT spatial_dim, array_agg(DISTINCT parent_location_code),"
        " array_agg(DISTINCT parent_location) FROM staging.who_observation"
        " WHERE run_id = %s AND parent_location_code IS NOT NULL GROUP BY 1", (run_id,)
    ).fetchall():
        if len(codes) > 1:
            raise ReferenceMismatch(f"WHO assigns {iso3} to several regions: {codes}")
        conn.execute(
            "UPDATE core.dim_country SET who_region_code = %s, who_region_name = %s WHERE iso3 = %s",
            (codes[0], names[0], iso3),
        )


def _measure_dependence(conn: psycopg.Connection, run_id: str, tolerance: float,
                        min_shared: int, min_share: float) -> None:
    """Which sources publish (nearly) the same value? Derived from the data, not asserted."""
    pairs = conn.execute(
        """
        SELECT i.concept, sa.source_code, sb.source_code, count(*) AS shared,
               count(*) FILTER (WHERE abs(a.value - b.value)
                                <= %(tol)s * greatest(abs(a.value), abs(b.value))) AS within,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY
                   CASE WHEN greatest(abs(a.value), abs(b.value)) = 0 THEN 0
                        ELSE abs(a.value - b.value) / greatest(abs(a.value), abs(b.value)) END)
        FROM core.fact_observation a
        JOIN core.fact_observation b
          ON b.run_id = a.run_id AND b.country_key = a.country_key
         AND b.indicator_key = a.indicator_key AND b.year = a.year
         AND b.source_key > a.source_key AND b.is_headline AND b.is_selected
        JOIN core.dim_source sa ON sa.source_key = a.source_key
        JOIN core.dim_source sb ON sb.source_key = b.source_key
        JOIN core.dim_indicator i ON i.indicator_key = a.indicator_key
        WHERE a.run_id = %(run_id)s AND a.is_headline AND a.is_selected
        GROUP BY 1, 2, 3
        """,
        {"run_id": run_id, "tol": tolerance},
    ).fetchall()

    parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for concept, a, b, shared, within, median in pairs:
        share = within / shared if shared else None
        dependent = bool(shared >= min_shared and share is not None and share >= min_share)
        conn.execute(
            "INSERT INTO core.source_dependence (run_id, concept, source_a, source_b, shared_cells,"
            " within_tolerance, share_within, median_rel_diff, tolerance, dependent)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (run_id, concept, a, b, shared, within, share, median, tolerance, dependent),
        )
        if dependent:
            parent[find((concept, a))] = find((concept, b))

    present = conn.execute(
        "SELECT DISTINCT i.concept, s.source_code FROM core.fact_observation f"
        " JOIN core.dim_source s USING (source_key) JOIN core.dim_indicator i USING (indicator_key)"
        " WHERE f.run_id = %s AND f.is_headline AND f.is_selected", (run_id,)
    ).fetchall()
    members: dict[tuple[str, str], list[str]] = {}
    for concept, source in present:
        members.setdefault(find((concept, source)), []).append(source)
    for concept, source in present:
        label = "+".join(sorted(members[find((concept, source))]))
        conn.execute(
            "INSERT INTO core.evidence_group (run_id, concept, source_code, group_label)"
            " VALUES (%s, %s, %s, %s)", (run_id, concept, source, label),
        )


def build_core(
    conn: psycopg.Connection,
    run_dir: Path,
    *,
    dependence_tolerance: float = DEPENDENCE_TOLERANCE,
    dependence_min_shared: int = DEPENDENCE_MIN_SHARED,
    dependence_min_share: float = DEPENDENCE_MIN_SHARE,
    conflict_tolerance: float = CONFLICT_TOLERANCE,
) -> dict:
    started = time.perf_counter()
    manifest = json.loads((Path(run_dir) / MANIFEST_NAME).read_text(encoding="utf-8"))
    run_id = manifest["run_id"]
    years = tuple(manifest["scope"]["years"])
    params = {"run_id": run_id, "year_min": years[0], "year_max": years[1],
              "conflict_tolerance": conflict_tolerance}

    with conn.transaction():
        if not conn.execute(
            "SELECT 1 FROM staging.load_log WHERE run_id = %s LIMIT 1", (run_id,)
        ).fetchone():
            raise RuntimeError(f"snapshot {run_id} is not loaded; run `python -m healthbridge.staging load`")
        load_reference(conn, years)
        _check_against_staging(conn, run_id)

        for table in ("fact_reconciled", "evidence_group", "source_dependence", "fact_observation",
                      "rejected_record", "build_log"):
            conn.execute(f"DELETE FROM core.{table} WHERE run_id = %s", (run_id,))

        # Explicit drops: ON COMMIT DROP only fires for the outermost transaction.
        conn.execute("DROP TABLE IF EXISTS pg_temp.core_std, pg_temp.core_classified")
        conn.execute(_sql("10_standardize.sql"), params)
        conn.execute(_sql("20_classify.sql"), params)
        staged = conn.execute("SELECT count(*) FROM core_classified").fetchone()[0]
        conn.execute(_sql("30_rejected.sql"), params)
        conn.execute(_sql("40_fact.sql"), params)
        conn.execute("DROP TABLE pg_temp.core_std, pg_temp.core_classified")

        _measure_dependence(conn, run_id, dependence_tolerance, dependence_min_shared,
                            dependence_min_share)
        conn.execute(_sql("60_reconcile.sql"), params)

        counts = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE is_headline),"
            " count(*) FILTER (WHERE is_selected) FROM core.fact_observation WHERE run_id = %s",
            (run_id,),
        ).fetchone()
        rejected = conn.execute(
            "SELECT count(*) FROM core.rejected_record WHERE run_id = %s", (run_id,)
        ).fetchone()[0]
        summary = {"run_id": run_id, "staged_rows": staged, "rows_loaded": counts[0],
                   "rows_rejected": rejected, "headline_rows": counts[1], "selected_rows": counts[2]}
        conn.execute(
            "INSERT INTO core.build_log (run_id, staged_rows, rows_loaded, rows_rejected,"
            " headline_rows, selected_rows, duration_seconds, parameters)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (run_id, staged, counts[0], rejected, counts[1], counts[2],
             time.perf_counter() - started,
             json.dumps({"dependence_tolerance": dependence_tolerance,
                         "dependence_min_shared": dependence_min_shared,
                         "dependence_min_share": dependence_min_share,
                         "conflict_tolerance": conflict_tolerance})),
        )
    return summary
