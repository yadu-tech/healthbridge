"""Data dictionary generated from the live warehouse schema.

    python -m healthbridge.dictionary --out docs/data_dictionary.md

Column names, types and nullability come from ``information_schema``, so they cannot drift from the
database. Descriptions are written by hand below; a test fails if a table or view has none.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

from healthbridge.db import connect

SCHEMAS = ("staging", "core", "marts", "dq")

# schema.table -> (grain, purpose)
TABLES: dict[str, tuple[str, str]] = {
    "staging.who_observation": ("one row per WHO GHO record", "WHO values as published, typed but not cleaned. Dimension columns (dim1..dim3) hold sex, wealth and similar breakdowns."),
    "staging.worldbank_observation": ("one row per World Bank country-year record", "World Bank values as published, including empty placeholder rows."),
    "staging.unicef_observation": ("one row per UNICEF SDMX record", "UNICEF values as published. Dataflows have different dimension columns; unshared ones are in `extra`."),
    "staging.load_log": ("one row per raw file loaded", "Checksum and row count of every file loaded, for lineage and idempotent loading."),
    "staging.v_observation": ("view over the three staging tables", "Source-shaped rows with common column names, as an analyst would first query them. Used for the 'before' measurements."),
    "core.dim_country": ("one row per African state", "The 54 states with ISO codes, UN sub-region and WHO region. Seven states lie outside WHO's AFR region."),
    "core.country_alias": ("one row per accepted spelling", "Normalized name variants and aliases resolved to an ISO3 code."),
    "core.dim_indicator": ("one row per harmonized concept", "The seven concepts with unit, definition and the plausible range used by the validity checks."),
    "core.indicator_source_map": ("one row per concept and source series", "Which source series code feeds which concept."),
    "core.dim_source": ("one row per source", "Sources and their reconciliation priority (fixed and arbitrary)."),
    "core.dim_year": ("one row per year", "Year dimension; the data is annual, so no full date dimension is kept."),
    "core.map_vocab": ("one row per source category value", "Crosswalk from each source's sex, wealth, residence and similar codes to the canonical vocabulary."),
    "core.fact_observation": ("source, country, indicator, year, sex, wealth quintile, residence, maternal education, age group, upstream series", "Every valid observation at an explicit grain, with lineage, quality flags and selection flags. Nothing is deleted to force uniqueness."),
    "core.rejected_record": ("one row per rejected staging row", "Rows not loaded into the fact table, each with a reason."),
    "core.evidence_group": ("one row per concept and source, per run", "Which sources publish the same underlying estimate (derived from the data)."),
    "core.source_dependence": ("one row per source pair and concept, per run", "The agreement statistics from which source dependence is derived."),
    "core.fact_reconciled": ("country, indicator, year, per run", "One value per cell with provenance. The value is taken from the highest-priority source of the highest-priority group, never averaged."),
    "core.build_log": ("one row per core build", "Counts, duration and parameters of each build."),
    "core.v_headline_observation": ("view: source, country, indicator, year", "The default analyst surface: one selected national-total row per source and cell."),
    "core.v_observation_dq": ("view over the fact table", "Fact rows in the shape the data-quality engine measures."),
    "marts.country_indicator_year": ("country, indicator, year, per run", "The analysis panel: reconciled values with quality tier and flags."),
    "marts.country_latest": ("country and indicator, per run", "Latest value per country and indicator, with how far behind it is."),
    "marts.trend": ("country, indicator and window, per run", "Absolute change, linear slope and annual percentage change over a window."),
    "marts.region_year": ("sub-region, indicator, year, per run", "Summary of countries (not of people) in a region, with coverage."),
    "marts.indicator_association": ("indicator pair, basis and reference, per run", "Cross-country Spearman correlations with Fisher-z intervals. Associations, not causes."),
    "marts.equity_gap": ("country, indicator, year and dimension, per run", "Gaps between groups within a single survey (for example poorest and richest)."),
    "marts.data_trust": ("indicator, per run", "How far each indicator can be trusted: coverage, cross-validation and flag shares."),
    "marts.build_log": ("one row per marts build", "Row counts and parameters of each build."),
    "marts.latest_run": ("single row", "Run identifier of the most recent marts build; the dashboard reads this."),
    "marts.v_panel": ("view over the panel", "The panel joined to country and indicator names."),
    "marts.v_country_latest": ("view over the latest table", "Latest values joined to names."),
    "dq.run": ("one row per measurement run", "A measurement of one snapshot at one stage (staging or core)."),
    "dq.metric": ("one row per metric per run", "Every data-quality metric with numerator and denominator, so scores are reproducible and queryable."),
}

# column name -> note. Applies to every table with that column.
COLUMNS: dict[str, str] = {
    "run_id": "Snapshot (raw ingestion run) identifier, a UTC timestamp.",
    "source_file": "Raw file the row came from (lineage).",
    "row_num": "Row position within the raw file (lineage).",
    "staging_source_file": "Raw file of the originating staging row (lineage).",
    "staging_row_num": "Row position of the originating staging row (lineage).",
    "value_text": "Value as published text, kept so unparseable values can still be counted.",
    "obs_value": "Parsed value; NULL where the text could not be parsed.",
    "numeric_value": "Parsed value; NULL where the text could not be parsed.",
    "lower_bound": "Lower uncertainty bound from the source, where published.",
    "upper_bound": "Upper uncertainty bound from the source, where published.",
    "low": "Lower uncertainty bound from the source, where published.",
    "high": "Upper uncertainty bound from the source, where published.",
    "extra": "Dimension columns that only some UNICEF dataflows have.",
    "unexpected_columns": "Columns present in the raw file but not in the expected schema.",
    "sha256": "SHA-256 of the raw file, verified against the manifest before loading.",
    "period_text": "Period as published (flagged when not a plain year).",
    "sex": "Canonical sex category, or the total.",
    "wealth_quintile": "Canonical wealth quintile, or the total.",
    "residence": "Canonical residence category, or the total.",
    "maternal_education": "Canonical maternal education category, or the total.",
    "age_group": "Age group; the headline age is defined per indicator.",
    "upstream_label": "Label of the upstream series or survey, used to keep comparisons within one survey.",
    "other_dims": "Dimensions without a canonical column.",
    "is_headline": "True for national-total rows at the indicator's defined age group.",
    "is_selected": "True for the row chosen for the default surface when several candidates exist.",
    "n_candidates": "Number of candidate rows for the cell before selection.",
    "selection_rule": "Rule that chose this row (producer priority, then latest period).",
    "quality_flags": "Flags raised for this row, for example temporal_outlier or noncanonical_period.",
    "country_resolution": "How the country code was resolved: exact, normalized, iso2 or alias.",
    "n_sources": "Number of sources with a value for the cell.",
    "n_evidence_groups": "Number of independent evidence groups; 1 means agreement is not independent confirmation.",
    "reconciled_value": "Value of the highest-priority source of the highest-priority group; never an average.",
    "reconciled_source_key": "Source that supplied the reconciled value.",
    "reconciled_source": "Source that supplied the reconciled value.",
    "spread_abs": "Absolute difference between evidence groups.",
    "spread_rel": "Difference between evidence groups divided by their mean.",
    "conflict": "True when independent evidence groups differ by more than 10%.",
    "group_values": "Value per evidence group; use this, not the single reconciled value, where there is a conflict.",
    "max_within_group_spread_rel": "Largest relative difference between sources inside one evidence group.",
    "within_group_disagreement": "True when sources of one group differ by more than 5%.",
    "shared_cells": "Country-years both sources have a value for.",
    "within_tolerance": "Shared cells that agree within the tolerance.",
    "share_within": "within_tolerance divided by shared_cells.",
    "median_rel_diff": "Median relative difference over shared cells.",
    "dependent": "True when at least 30 shared cells agree within 1% in at least 90% of cases.",
    "reason": "Why the row was rejected, for example no_value or unknown_country.",
    "quality_tier": "single_evidence, cross_validated or conflict.",
    "outlier_flag": "True when the temporal outlier check flagged the value.",
    "source_disagreement": "True when sources inside the evidence group disagree beyond the tolerance.",
    "years_behind": "Years between the latest value and the end of the snapshot window.",
    "annual_pct_change": "Compound annual percentage change (log-linear fit).",
    "linear_slope_per_year": "Slope of a linear fit, in indicator units per year.",
    "coverage_share": "Share of the region's countries with a value.",
    "grid_coverage": "Share of the country-year grid with a value.",
    "share_cross_validated": "Share of values supported by two or more independent groups.",
    "share_single_evidence": "Share of values with a single evidence group.",
    "share_conflict": "Share of values in conflict.",
    "rho_ci_low": "Lower end of the 95% Fisher-z interval (treats countries as independent, so understates uncertainty).",
    "rho_ci_high": "Upper end of the 95% Fisher-z interval (treats countries as independent, so understates uncertainty).",
    "basis": "Whether the association is between levels or between changes.",
    "details": "JSON with metric-specific detail.",
    "parameters": "JSON of the thresholds and settings used for the build.",
    "dimension": "Quality dimension (completeness, validity, uniqueness, consistency, integration) or vocabulary dimension.",
    "scope": "Source or 'all' that the metric covers.",
    "stage": "staging or core.",
    "valid_min": "Plausible minimum used by the range check.",
    "valid_max": "Plausible maximum used by the range check.",
    "priority": "Reconciliation priority; lower wins. Arbitrary and fixed.",
}


# Columns whose meaning differs by layer: staging keeps the source's own codes.
STAGING_NOTES: dict[str, str] = {
    "sex": "Source code as published (WHO and UNICEF code the same categories differently).",
    "wealth_quintile": "Source code as published.",
    "residence": "Source code as published.",
}


def _note(table: str, column: str) -> str:
    if table.startswith("staging.") and column in STAGING_NOTES:
        return STAGING_NOTES[column]
    return COLUMNS.get(column, "")


def _columns(conn: psycopg.Connection) -> dict[str, list[tuple[str, str, str]]]:
    rows = conn.execute(
        "SELECT table_schema || '.' || table_name, column_name, data_type, is_nullable"
        " FROM information_schema.columns WHERE table_schema = ANY(%s)"
        " ORDER BY table_schema, table_name, ordinal_position",
        (list(SCHEMAS),),
    ).fetchall()
    out: dict[str, list[tuple[str, str, str]]] = {}
    for table, column, dtype, nullable in rows:
        out.setdefault(table, []).append((column, dtype, "yes" if nullable == "YES" else "no"))
    return out


def undocumented(conn: psycopg.Connection) -> list[str]:
    """Tables and views in the warehouse that have no description here."""
    return sorted(set(_columns(conn)) - set(TABLES))


def render(conn: psycopg.Connection) -> str:
    cols = _columns(conn)
    lines = [
        "# Data dictionary",
        "",
        ("Generated by `python -m healthbridge.dictionary` from the live schema; column names, types and "
         "nullability cannot drift from the database. Descriptions are maintained in "
         "`src/healthbridge/dictionary.py`."),
        "",
        ("Layers: `staging` (source-shaped), `core` (integrated), `marts` (analysis-ready), `dq` (data-quality "
         "measurements). The `raw` layer is files on disk (`data/raw/<run_id>/`, with `manifest.json`), not tables. "
         "The `ml` schema is reserved but empty: the forecasting study reads the marts panel and keeps its working "
         "data in memory and in result files."),
        "",
    ]
    for schema in SCHEMAS:
        lines += [f"## {schema}", ""]
        for table in sorted(t for t in cols if t.startswith(schema + ".")):
            grain, purpose = TABLES.get(table, ("", "(no description)"))
            kind = "view" if table.split(".")[1].startswith("v_") else "table"
            lines += [f"### `{table}` ({kind})", "", purpose, ""]
            if grain:
                lines += [f"**Grain:** {grain}.", ""]
            lines += ["| Column | Type | Null | Note |", "|---|---|---|---|"]
            for name, dtype, nullable in cols[table]:
                note = _note(table, name)
                lines.append(f"| `{name}` | {dtype} | {nullable} | {note} |")
            lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m healthbridge.dictionary")
    parser.add_argument("--out", type=Path, help="write to this file instead of stdout")
    args = parser.parse_args(argv)
    with connect() as conn:
        missing = undocumented(conn)
        if missing:
            print(f"undocumented: {', '.join(missing)}", file=sys.stderr)
            return 1
        text = render(conn)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
