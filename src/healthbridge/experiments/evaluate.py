"""Evaluate what the pipeline did with each injected fault.

Outcome labels for a row-level fault, from the pipeline's own tables:

    rejected:<reason>   the row is in core.rejected_record
    flagged:<flag>      the row (or its reconciled cell) carries a quality flag
    recovered           the row was loaded with its original country, year and value
    silent              the row was loaded with different content and no flag: an error got through
    lost                the row is neither loaded nor rejected (should not happen)

A fault is *handled correctly* when at least one label is in the fault's expected set. The
comparison baseline is a naive loader that parses each row, joins on the exact ISO3 string and
drops nulls; it has no validation, so it shows what an unvalidated pipeline would let through.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

import psycopg

from healthbridge.experiments.faults import Fault, PoolRow, rows_of
from healthbridge.reference import read_csv
from healthbridge.staging import parse
from healthbridge.staging.schema import SchemaReport

PRIORITY = ("flagged:temporal_outlier", "flagged:within_group_disagreement", "flagged:conflict",
            "flagged:bounds_violation", "flagged:noncanonical_period")
ROW_FLAGS = ("temporal_outlier", "bounds_violation", "noncanonical_period")
PARSERS = {"who": parse.parse_who, "worldbank": parse.parse_worldbank, "unicef": parse.parse_unicef}
PARSED_FIELDS = {"who": ("spatial_dim", "time_dim", "numeric_value"),
                 "worldbank": ("country_iso3", "year", "value"),
                 "unicef": ("ref_area", "year", "obs_value")}


@dataclass
class Outcomes:
    rejected: dict[tuple[str, int], str]
    facts: dict[tuple[str, int], dict]
    recon: dict[tuple[str, str, int], dict]


def read_outcomes(conn: psycopg.Connection, run_id: str) -> Outcomes:
    rejected = {(f, r): reason for f, r, reason in conn.execute(
        "SELECT source_file, row_num, reason FROM core.rejected_record WHERE run_id = %s",
        (run_id,)).fetchall()}
    facts = {(f, r): {"iso3": iso3.strip(), "year": year, "value": value, "flags": list(flags),
                      "resolution": res}
             for f, r, iso3, year, value, flags, res in conn.execute(
        "SELECT f.staging_source_file, f.staging_row_num, c.iso3, f.year, f.value, f.quality_flags,"
        " f.country_resolution FROM core.fact_observation f JOIN core.dim_country c USING (country_key)"
        " WHERE f.run_id = %s", (run_id,)).fetchall()}
    recon = {(concept, iso3.strip(), year): {"conflict": conflict, "within_group": within}
             for concept, iso3, year, conflict, within in conn.execute(
        "SELECT i.concept, c.iso3, r.year, r.conflict, r.within_group_disagreement"
        " FROM core.fact_reconciled r JOIN core.dim_indicator i USING (indicator_key)"
        " JOIN core.dim_country c USING (country_key) WHERE r.run_id = %s", (run_id,)).fetchall()}
    return Outcomes(rejected, facts, recon)


def _close(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def classify(fault: Fault, outcomes: Outcomes) -> list[str]:
    """All labels for one row-level fault, most informative first."""
    key = (fault.file, fault.row_num)
    if key in outcomes.rejected:
        return [f"rejected:{outcomes.rejected[key]}"]
    fact = outcomes.facts.get(key)
    if fact is None:
        return ["lost"]
    labels = [f"flagged:{flag}" for flag in ROW_FLAGS if flag in fact["flags"]]
    cell = outcomes.recon.get((fault.concept, fault.iso3, fault.year))
    if cell:
        if cell["within_group"]:
            labels.append("flagged:within_group_disagreement")
        if cell["conflict"]:
            labels.append("flagged:conflict")
    if labels:
        return sorted(labels, key=lambda label: PRIORITY.index(label) if label in PRIORITY else 99)
    unchanged = (fact["iso3"] == fault.iso3 and fact["year"] == fault.year
                 and _close(fact["value"], fault.orig_value))
    return ["recovered" if unchanged else "silent"]


def classify_schema(fault: Fault, reports: dict[str, SchemaReport]) -> list[str]:
    report = reports[fault.file]
    if report.missing:
        return ["schema:error"]
    return ["schema:warned"] if report.unexpected else ["silent"]


def naive_outcome(fault: Fault, parsed_rows: dict[str, list[dict]], canonical: set[str]) -> str | None:
    """Result of a loader that parses, joins on the exact ISO3 string and drops nulls.

    accepted_correct / accepted_wrong / dropped; None where the idea does not apply
    (categorical codes are not used by a loader that ignores dimensions).
    """
    if fault.fault_type == "categorical_invalid":
        return None
    row = parsed_rows[fault.file][fault.row_num - 1]
    country_field, year_field, value_field = PARSED_FIELDS[fault.source]
    iso3, year, value = row[country_field], row[year_field], row[value_field]
    if value is None or year is None or iso3 not in canonical:
        return "dropped"
    if fault.fault_type == "duplicate":
        return "accepted_wrong"   # the same observation is counted twice
    correct = iso3 == fault.iso3 and year == fault.year and _close(value, fault.orig_value)
    return "accepted_correct" if correct else "accepted_wrong"


def evaluate_run(faults: list[Fault], outcomes: Outcomes, corrupted_files: dict[str, bytes],
                 sources: dict[str, str]) -> list[dict]:
    """One result record per fault, with pipeline labels and the naive-loader outcome."""
    canonical = {r["iso3"] for r in read_csv("countries.csv")}
    parsed_cache: dict[str, list[dict]] = {}
    results = []
    for fault in faults:
        if fault.file not in parsed_cache:
            parsed_cache[fault.file] = PARSERS[sources[fault.file]](corrupted_files[fault.file])
        labels = classify(fault, outcomes)
        results.append({
            "fault_type": fault.fault_type, "variant": fault.variant, "source": fault.source,
            "concept": fault.concept, "labels": labels, "expected": fault.expected,
            "correct": bool(set(labels) & set(fault.expected)),
            "silent": labels == ["silent"],
            "naive": naive_outcome(fault, parsed_cache, canonical),
            "n_neighbors": fault.n_neighbors, "cell_members": fault.cell_members,
            "key": (fault.file, fault.row_num, fault.concept, fault.iso3, fault.year),
        })
    return results


def false_positives(universe: list[PoolRow], outcomes: Outcomes, injected: list[Fault]) -> dict[str, int]:
    """Alerts on rows that were clean in the unmodified run and were not touched.

    Row-level mechanisms count rows; reconciled-cell mechanisms count distinct cells.
    """
    touched = {(f.file, f.row_num) for f in injected} | {(f.file, f.origin_row_num) for f in injected
                                                          if f.origin_row_num}
    touched_cells = {(f.concept, f.iso3, f.year) for f in injected}
    counts: Counter = Counter()
    seen_cells: set[tuple] = set()
    for row in universe:
        key = (row.file, row.row_num)
        if key in touched:
            continue
        if key in outcomes.rejected:
            counts[f"rejected:{outcomes.rejected[key]}"] += 1
            continue
        fact = outcomes.facts.get(key)
        if fact:
            for flag in ROW_FLAGS:
                if flag in fact["flags"]:
                    counts[f"flagged:{flag}"] += 1
        cell = (row.concept, row.iso3, row.year)
        if cell in touched_cells or cell in seen_cells:
            continue
        seen_cells.add(cell)
        state = outcomes.recon.get(cell)
        if state:
            if state["within_group"]:
                counts["flagged:within_group_disagreement"] += 1
            if state["conflict"]:
                counts["flagged:conflict"] += 1
    return dict(counts)


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion."""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def clean_alert_rates(conn: psycopg.Connection, clean_run_id: str) -> dict:
    """How often each check fires on the unmodified snapshot (the false-alarm context)."""
    def one(sql, *a):
        return conn.execute(sql, a).fetchone()
    rows, outliers, bounds, noncanon = one(
        "SELECT count(*), count(*) FILTER (WHERE 'temporal_outlier' = ANY(quality_flags)),"
        " count(*) FILTER (WHERE 'bounds_violation' = ANY(quality_flags)),"
        " count(*) FILTER (WHERE 'noncanonical_period' = ANY(quality_flags))"
        " FROM core.fact_observation WHERE run_id = %s AND is_headline AND is_selected", clean_run_id)
    cells, within, conflict = one(
        "SELECT count(*), count(*) FILTER (WHERE within_group_disagreement),"
        " count(*) FILTER (WHERE conflict) FROM core.fact_reconciled WHERE run_id = %s", clean_run_id)
    staged = one("SELECT sum(rows_loaded) FROM staging.load_log WHERE run_id = %s", clean_run_id)[0]
    rejected = dict(conn.execute("SELECT reason, count(*) FROM core.rejected_record WHERE run_id = %s"
                                 " GROUP BY 1", (clean_run_id,)).fetchall())
    return {"selected_rows": rows, "temporal_outlier": outliers, "bounds_violation": bounds,
            "noncanonical_period": noncanon, "reconciled_cells": cells,
            "within_group_disagreement": within, "conflict": conflict,
            "staged_rows": int(staged), "rejected": rejected}


def corrupted_bytes(parsed_rows_source: dict[str, bytes]) -> dict[str, bytes]:
    return parsed_rows_source


__all__ = [
    "Outcomes",
    "classify",
    "classify_schema",
    "clean_alert_rates",
    "evaluate_run",
    "false_positives",
    "read_outcomes",
    "rows_of",
    "wilson",
]
