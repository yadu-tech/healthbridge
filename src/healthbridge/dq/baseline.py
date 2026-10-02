"""Data-quality baseline: measure the staged (not yet cleaned) data.

Every metric is stored long-form in ``dq.metric`` and is a pure function of one snapshot, so
the "before" state of the data is reproducible and queryable. The same engine is meant to be
re-run on the cleaned/integrated layer to produce the "after" numbers.

Dimensions
----------
completeness  share of rows with a value (row level) and grid coverage of country-years
validity      rows that are unparseable, out of plausible range, outside their own uncertainty
              bounds, or carry an invalid year or country code
uniqueness    exact duplicate rows, and rows that share (country, year) with other rows, i.e.
              ambiguity at the grain analysts actually use
consistency   period values that are not a plain four-digit year
integration   schema/vocabulary heterogeneity and what a naive cross-source join does

Composite score: unweighted mean of completeness, validity, uniqueness-at-grain and consistency,
each in [0, 1]. Weights are arbitrary, so the report also shows a leave-one-dimension-out range.
"""
from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from healthbridge.ingest.snapshot import MANIFEST_NAME
from healthbridge.reference import indicator_ranges

# Plausible closed ranges per concept (inclusive), from reference/indicators.csv.
EXPECTED_RANGES: dict[str, tuple[float, float]] = indicator_ranges()
SCORE_DIMENSIONS = ("completeness", "validity", "uniqueness", "consistency")


@dataclass
class Metric:
    scope: str
    dimension: str
    metric: str
    value: float | None
    source: str = ""
    concept: str = ""
    numerator: int | None = None
    denominator: int | None = None
    details: dict | list | None = field(default=None)


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def _row_level(conn, countries: list[str], years: tuple[int, int]) -> list[Metric]:
    ranges = list(EXPECTED_RANGES.items())
    values_sql = ", ".join(["(%s, %s::float8, %s::float8)"] * len(ranges))
    params: list = [x for concept, (lo, hi) in ranges for x in (concept, lo, hi)]
    params += [years[0], years[1], countries]
    rows = conn.execute(
        f"""
        WITH ranges(concept, lo, hi) AS (VALUES {values_sql}),
        o AS (SELECT obs.*, r.lo, r.hi FROM obs LEFT JOIN ranges r USING (concept))
        SELECT source, concept,
          count(*) AS n,
          count(*) FILTER (WHERE value IS NULL) AS missing,
          count(*) FILTER (WHERE value IS NULL AND value_text IS NOT NULL) AS unparseable,
          count(*) FILTER (WHERE value IS NOT NULL AND (value < lo OR value > hi)) AS out_of_range,
          count(*) FILTER (WHERE value IS NOT NULL AND low IS NOT NULL AND high IS NOT NULL
                           AND (value < low OR value > high)) AS bounds_violation,
          count(*) FILTER (WHERE year IS NULL OR year < %s OR year > %s) AS invalid_year,
          count(*) FILTER (WHERE iso3 IS NULL OR NOT (iso3 = ANY(%s))) AS invalid_country,
          count(*) FILTER (WHERE period_text IS NULL OR period_text !~ '^[0-9]{{4}}$') AS noncanonical,
          count(*) FILTER (WHERE (value IS NULL AND value_text IS NOT NULL)
              OR (value IS NOT NULL AND (value < lo OR value > hi))
              OR (value IS NOT NULL AND low IS NOT NULL AND high IS NOT NULL
                  AND (value < low OR value > high))
              OR year IS NULL OR year < %s OR year > %s
              OR iso3 IS NULL OR NOT (iso3 = ANY(%s))) AS invalid_rows,
          count(*) - count(DISTINCT (iso3, period_text, dim_key, value_text)) AS exact_duplicates,
          count(DISTINCT iso3) AS countries
        FROM o GROUP BY source, concept ORDER BY source, concept
        """,
        params + [years[0], years[1], countries],
    ).fetchall()

    out: list[Metric] = []
    for (source, concept, n, missing, unparseable, out_of_range, bounds, bad_year, bad_country,
         noncanonical, invalid_rows, exact_dups, n_countries) in rows:
        def m(dimension, metric, num, _src=source, _con=concept, _n=n):
            out.append(Metric("series", dimension, metric, _ratio(num, _n), _src, _con, num, _n))

        out.append(Metric("series", "completeness", "records_received", float(n), source, concept, n, None))
        m("completeness", "missing_rate", missing)
        m("validity", "unparseable_rate", unparseable)
        m("validity", "out_of_range_rate", out_of_range)
        m("validity", "bounds_violation_rate", bounds)
        m("validity", "invalid_year_rate", bad_year)
        m("validity", "invalid_country_rate", bad_country)
        m("validity", "invalid_row_rate", invalid_rows)
        m("uniqueness", "exact_duplicate_rate", exact_dups)
        m("consistency", "noncanonical_period_rate", noncanonical)
        out.append(Metric("series", "completeness", "countries_present", float(n_countries), source, concept))
    return out


def _coverage_and_grain(conn, countries: list[str], years: tuple[int, int]) -> list[Metric]:
    expected = len(countries) * (years[1] - years[0] + 1)
    out: list[Metric] = []
    cover = conn.execute(
        """
        SELECT source, concept,
               count(DISTINCT (iso3, year)) FILTER (WHERE value IS NOT NULL
                   AND year BETWEEN %s AND %s AND iso3 = ANY(%s)) AS cells
        FROM obs GROUP BY source, concept
        """,
        (years[0], years[1], countries),
    ).fetchall()
    for source, concept, cells in cover:
        out.append(Metric("series", "completeness", "grid_coverage", _ratio(cells, expected),
                          source, concept, cells, expected))

    grain = conn.execute(
        """
        WITH g AS (SELECT source, concept, iso3, year, count(*) AS c FROM obs
                   WHERE year IS NOT NULL AND iso3 IS NOT NULL GROUP BY 1, 2, 3, 4),
        n AS (SELECT source, concept, count(*) AS n FROM obs GROUP BY 1, 2)
        SELECT g.source, g.concept, n.n,
               count(*) FILTER (WHERE c > 1) AS ambiguous_cells,
               coalesce(sum(c) FILTER (WHERE c > 1), 0) AS rows_in_ambiguous,
               count(*) AS cells
        FROM g JOIN n USING (source, concept) GROUP BY g.source, g.concept, n.n
        """
    ).fetchall()
    for source, concept, n, ambiguous_cells, rows_in_ambiguous, cells in grain:
        out.append(Metric("series", "uniqueness", "grain_ambiguity_rate",
                          _ratio(int(rows_in_ambiguous), n), source, concept, int(rows_in_ambiguous), n))
        out.append(Metric("series", "uniqueness", "ambiguous_cell_rate",
                          _ratio(ambiguous_cells, cells), source, concept, ambiguous_cells, cells))
    return out


def _integration(conn) -> list[Metric]:
    out: list[Metric] = []
    fan = conn.execute(
        """
        WITH s AS (SELECT concept, source, iso3, year, count(*) AS c FROM obs
                   WHERE year IS NOT NULL AND iso3 IS NOT NULL GROUP BY 1, 2, 3, 4),
        w AS (SELECT * FROM s WHERE source = 'who'),
        b AS (SELECT * FROM s WHERE source = 'worldbank'),
        u AS (SELECT * FROM s WHERE source = 'unicef')
        SELECT w.concept, count(*) AS shared_cells, sum(w.c * b.c * u.c) AS naive_rows
        FROM w JOIN b USING (concept, iso3, year) JOIN u USING (concept, iso3, year)
        GROUP BY w.concept
        """
    ).fetchall()
    for concept, shared_cells, naive_rows in fan:
        out.append(Metric("concept", "integration", "naive_join_fanout",
                          _ratio(int(naive_rows), shared_cells), concept=concept,
                          numerator=int(naive_rows), denominator=shared_cells))
    any_cells = dict(conn.execute(
        "SELECT concept, count(DISTINCT (iso3, year)) FROM obs"
        " WHERE value IS NOT NULL AND year IS NOT NULL GROUP BY 1"
    ).fetchall())
    for concept, shared_cells, _ in fan:
        out.append(Metric("concept", "integration", "cells_in_all_three_sources",
                          _ratio(shared_cells, any_cells[concept]), concept=concept,
                          numerator=shared_cells, denominator=any_cells[concept]))

    conflicts = conn.execute(
        """
        SELECT w.concept,
               count(*) AS total,
               count(*) FILTER (WHERE greatest(w.value, b.value, u.value) - least(w.value, b.value, u.value)
                                      > 0.10 * (w.value + b.value + u.value) / 3.0) AS conflicting
        FROM obs w
        JOIN obs b ON b.concept = w.concept AND b.iso3 = w.iso3 AND b.year = w.year
                  AND b.source = 'worldbank' AND b.value IS NOT NULL
        JOIN obs u ON u.concept = w.concept AND u.iso3 = w.iso3 AND u.year = w.year
                  AND u.source = 'unicef' AND u.value IS NOT NULL
        WHERE w.source = 'who' AND w.value IS NOT NULL
        GROUP BY w.concept
        """
    ).fetchall()
    for concept, total, conflicting in conflicts:
        out.append(Metric("concept", "integration", "naive_join_conflict_rate",
                          _ratio(conflicting, total), concept=concept,
                          numerator=conflicting, denominator=total))
    return out


def _vocabulary(conn, run_id: str) -> list[Metric]:
    """How each source encodes the same categorical ideas (sex, wealth quintile, region)."""
    queries = {
        ("who", "sex_values"): "SELECT DISTINCT dim1 FROM staging.who_observation"
                               " WHERE run_id = %s AND dim1_type = 'SEX' ORDER BY 1",
        ("unicef", "sex_values"): "SELECT DISTINCT sex FROM staging.unicef_observation"
                                  " WHERE run_id = %s AND sex IS NOT NULL ORDER BY 1",
        ("who", "wealth_values"): "SELECT DISTINCT dim3 FROM staging.who_observation"
                                  " WHERE run_id = %s AND dim3_type = 'WEALTHQUINTILE' ORDER BY 1",
        ("unicef", "wealth_values"): "SELECT DISTINCT wealth_quintile FROM staging.unicef_observation"
                                     " WHERE run_id = %s AND wealth_quintile IS NOT NULL ORDER BY 1",
    }
    out: list[Metric] = []
    for (source, name), sql in queries.items():
        values = [r[0] for r in conn.execute(sql, (run_id,)).fetchall()]
        out.append(Metric("source", "integration", f"distinct_{name}", float(len(values)),
                          source, details=values))
    regions = conn.execute(
        "SELECT parent_location_code, array_agg(DISTINCT spatial_dim ORDER BY spatial_dim)"
        " FROM staging.who_observation WHERE run_id = %s GROUP BY 1 ORDER BY 1", (run_id,)
    ).fetchall()
    mapping = {code: isos for code, isos in regions}
    outside = sorted(iso for code, isos in mapping.items() if code != "AFR" for iso in isos)
    total = len({iso for isos in mapping.values() for iso in isos})
    out.append(Metric("source", "integration", "countries_outside_who_afr_region",
                      _ratio(len(outside), total), "who", numerator=len(outside), denominator=total,
                      details={"regions": mapping}))
    return out


def _schema_variants(run_dir: Path, manifest: dict) -> list[Metric]:
    """UNICEF dataflows expose different column sets; measure that from the raw CSV headers."""
    headers: dict[str, tuple[str, ...]] = {}
    for entry in manifest["files"]:
        if entry["source"] == "unicef":
            text = (run_dir / entry["path"]).read_text(encoding="utf-8-sig")
            headers[entry["series"]] = tuple(next(csv.reader(io.StringIO(text))))
    if not headers:
        return []
    shared = set.intersection(*(set(h) for h in headers.values()))
    union = set.union(*(set(h) for h in headers.values()))
    return [
        Metric("source", "integration", "distinct_column_sets", float(len(set(headers.values()))),
               "unicef", numerator=len(set(headers.values())), denominator=len(headers),
               details={s: list(h) for s, h in headers.items()}),
        Metric("source", "integration", "columns_shared_by_all_series", _ratio(len(shared), len(union)),
               "unicef", numerator=len(shared), denominator=len(union)),
    ]


def _core_metrics(conn, run_id: str) -> list[Metric]:
    """Pipeline accounting and reconciliation outcomes; only meaningful for the core layer."""
    out: list[Metric] = []
    staged = {(s, c): int(n) for s, c, n in conn.execute(
        "SELECT source, concept, sum(rows_loaded) FROM staging.load_log WHERE run_id = %s GROUP BY 1, 2",
        (run_id,)).fetchall()}
    rejected: dict[tuple[str, str], dict[str, int]] = {}
    for source, concept, reason, n in conn.execute(
        "SELECT source, concept, reason, count(*) FROM core.rejected_record WHERE run_id = %s GROUP BY 1, 2, 3",
        (run_id,)).fetchall():
        rejected.setdefault((source, concept), {})[reason] = n
    loaded = {(s, c): (n, h, sel) for s, c, n, h, sel in conn.execute(
        "SELECT s.source_code, i.concept, count(*), count(*) FILTER (WHERE f.is_headline),"
        " count(*) FILTER (WHERE f.is_selected) FROM core.fact_observation f"
        " JOIN core.dim_source s USING (source_key) JOIN core.dim_indicator i USING (indicator_key)"
        " WHERE f.run_id = %s GROUP BY 1, 2", (run_id,)).fetchall()}
    for (source, concept), n_staged in sorted(staged.items()):
        reasons = rejected.get((source, concept), {})
        n_loaded, _headline, n_selected = loaded.get((source, concept), (0, 0, 0))
        out.append(Metric("series", "lineage", "records_staged", float(n_staged), source, concept))
        out.append(Metric("series", "lineage", "records_rejected", _ratio(sum(reasons.values()), n_staged),
                          source, concept, sum(reasons.values()), n_staged, details=reasons))
        out.append(Metric("series", "lineage", "records_loaded", float(n_loaded), source, concept))
        out.append(Metric("series", "lineage", "records_selected", float(n_selected), source, concept,
                          n_selected, n_staged))
    for concept, cells, cross, conflicts in conn.execute(
        "SELECT i.concept, count(*), count(*) FILTER (WHERE n_evidence_groups > 1),"
        " count(*) FILTER (WHERE conflict) FROM core.fact_reconciled r"
        " JOIN core.dim_indicator i USING (indicator_key) WHERE r.run_id = %s GROUP BY 1",
        (run_id,)).fetchall():
        out.append(Metric("concept", "integration", "cells_reconciled", float(cells), concept=concept))
        out.append(Metric("concept", "integration", "cross_validated_cell_rate", _ratio(cross, cells),
                          concept=concept, numerator=cross, denominator=cells))
        out.append(Metric("concept", "integration", "conflict_rate_among_cross_validated",
                          _ratio(conflicts, cross), concept=concept, numerator=conflicts, denominator=cross))
    for concept, groups in conn.execute(
        "SELECT concept, array_agg(DISTINCT group_label ORDER BY group_label) FROM core.evidence_group"
        " WHERE run_id = %s GROUP BY 1", (run_id,)).fetchall():
        out.append(Metric("concept", "integration", "independent_evidence_groups", float(len(groups)),
                          concept=concept, details=groups))
    return out


def _scores(metrics: list[Metric]) -> list[Metric]:
    """Per-dimension scores in [0, 1] for each series, each source and overall."""
    rate_metric = {"completeness": "missing_rate", "validity": "invalid_row_rate",
                   "uniqueness": "grain_ambiguity_rate", "consistency": "noncanonical_period_rate"}
    # (source, concept) -> dimension -> (numerator, denominator)
    cells: dict[tuple[str, str], dict[str, tuple[int, int]]] = {}
    for m in metrics:
        if m.scope == "series" and rate_metric.get(m.dimension) == m.metric and m.denominator:
            cells.setdefault((m.source, m.concept), {})[m.dimension] = (m.numerator or 0, m.denominator)

    def emit(scope: str, source: str, concept: str, keys: list[tuple[str, str]]) -> list[Metric]:
        out: list[Metric] = []
        scores: dict[str, float] = {}
        for dim in SCORE_DIMENSIONS:
            num = sum(cells[k][dim][0] for k in keys if dim in cells[k])
            den = sum(cells[k][dim][1] for k in keys if dim in cells[k])
            if den:
                scores[dim] = 1 - num / den
                out.append(Metric(scope, "score", dim, scores[dim], source, concept, den - num, den))
        if len(scores) == len(SCORE_DIMENSIONS):
            composite = sum(scores.values()) / len(scores)
            leave_one_out = [sum(v for d, v in scores.items() if d != drop) / (len(scores) - 1)
                             for drop in scores]
            out.append(Metric(scope, "score", "composite", composite, source, concept,
                              details={"weights": "equal", "leave_one_out_min": min(leave_one_out),
                                       "leave_one_out_max": max(leave_one_out)}))
        return out

    result: list[Metric] = []
    for source, concept in sorted(cells):
        result += emit("series", source, concept, [(source, concept)])
    for source in sorted({s for s, _ in cells}):
        result += emit("source", source, "", [k for k in cells if k[0] == source])
    result += emit("overall", "", "", list(cells))
    return result


STAGE_RELATIONS = {"staging": "staging.v_observation", "core": "core.v_observation_dq"}


def compute_baseline(conn: psycopg.Connection, run_dir: Path, stage: str = "staging") -> int:
    """Measure one layer ('staging' or 'core') for the snapshot in ``run_dir``; returns dq_run_id.

    The same checks run on both layers so their results are directly comparable. For 'core' the
    measured surface is the default analyst view: one selected national-total row per source,
    country, indicator and year.
    """
    if stage not in STAGE_RELATIONS:
        raise ValueError(f"stage must be one of {sorted(STAGE_RELATIONS)}")
    started = time.perf_counter()
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    run_id = manifest["run_id"]
    countries = manifest["scope"]["countries"]
    years = tuple(manifest["scope"]["years"])

    with conn.transaction():
        ready_table = "staging.load_log" if stage == "staging" else "core.build_log"
        loaded = conn.execute(
            f"SELECT count(*) FROM {ready_table} WHERE run_id = %s", (run_id,)
        ).fetchone()[0]
        if not loaded:
            raise RuntimeError(f"snapshot {run_id} is not loaded into {stage}; run the previous step first")
        # Explicit drop (not ON COMMIT DROP): that only fires when the *outermost* transaction
        # commits, so a caller with a transaction already open would otherwise keep the table.
        conn.execute("DROP TABLE IF EXISTS pg_temp.obs")
        conn.execute("CREATE TEMP TABLE obs AS"
                     f" SELECT * FROM {STAGE_RELATIONS[stage]} WHERE run_id = %s", (run_id,))
        conn.execute("CREATE INDEX ON obs (concept, iso3, year, source)")
        conn.execute("ANALYZE obs")

        metrics = (_row_level(conn, countries, years) + _coverage_and_grain(conn, countries, years)
                   + _integration(conn))
        if stage == "staging":
            metrics += _vocabulary(conn, run_id) + _schema_variants(run_dir, manifest)
        else:
            metrics += _core_metrics(conn, run_id)
        metrics += _scores(metrics)

        conn.execute("DROP TABLE pg_temp.obs")
        duration = time.perf_counter() - started
        metrics.append(Metric("overall", "completeness", "processing_seconds", duration))
        dq_run_id = conn.execute(
            "INSERT INTO dq.run (snapshot_run_id, stage, duration_seconds, details)"
            " VALUES (%s, %s, %s, %s) RETURNING dq_run_id",
            (run_id, stage, duration, json.dumps({"years": list(years), "n_countries": len(countries),
                                                   "expected_ranges": EXPECTED_RANGES})),
        ).fetchone()[0]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO dq.metric (dq_run_id, scope, source, concept, dimension, metric,"
                " value, numerator, denominator, details) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [(dq_run_id, m.scope, m.source, m.concept, m.dimension, m.metric, m.value,
                  m.numerator, m.denominator, json.dumps(m.details) if m.details is not None else None)
                 for m in metrics],
            )
    return dq_run_id
