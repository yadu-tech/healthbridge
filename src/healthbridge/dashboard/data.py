"""Data access for the dashboard: SQL over the marts and the quality tables, returned as DataFrames.

No Streamlit here, so every function can be tested against a database. The dashboard reads only
the marts (plus the pipeline and data-quality log tables), never raw or staging data.
"""
from __future__ import annotations

import pandas as pd
import psycopg

UNIT_SHORT = {
    "under5_mortality": "per 1,000 live births", "neonatal_mortality": "per 1,000 live births",
    "maternal_mortality_ratio": "per 100,000 live births", "skilled_birth_attendance": "% of births",
    "dtp3_coverage": "% of one-year-olds", "measles_mcv1_coverage": "% of one-year-olds",
    "stunting_prevalence": "% of children under 5",
}
DIMENSIONS = {"wealth": "Wealth (poorest vs richest quintile)", "sex": "Sex (female vs male)",
              "residence": "Residence (rural vs urban)"}
BASES = {"level": "Levels across countries", "change": "Change between the base and reference year"}
TIER_LABELS = {"single_evidence_group": "Single evidence group", "cross_validated": "Cross-validated",
               "conflict": "Conflict"}
# Short names for axes and titles, where the full indicator name is too long to fit.
SHORT_NAMES = {
    "under5_mortality": "Under-5 mortality", "neonatal_mortality": "Neonatal mortality",
    "maternal_mortality_ratio": "Maternal mortality", "skilled_birth_attendance": "Skilled birth attendance",
    "dtp3_coverage": "DTP3 coverage", "measles_mcv1_coverage": "Measles (MCV1) coverage",
    "stunting_prevalence": "Stunting (under 5)",
}


def short_label(concept: str) -> str:
    """Short indicator name with its unit, e.g. 'DTP3 coverage (% of one-year-olds)'."""
    return f"{SHORT_NAMES.get(concept, concept)} ({UNIT_SHORT.get(concept, '')})"


def _df(conn: psycopg.Connection, sql: str, params: dict | tuple | None = None) -> pd.DataFrame:
    cur = conn.execute(sql, params)
    columns = [d.name for d in cur.description]
    return pd.DataFrame(cur.fetchall(), columns=columns)


def latest_run(conn: psycopg.Connection) -> str | None:
    row = conn.execute("SELECT run_id FROM marts.latest_run").fetchone()
    return row[0] if row else None


def snapshot_info(conn: psycopg.Connection) -> dict:
    run_id = latest_run(conn)
    if run_id is None:
        raise LookupError("no marts have been built; run `python -m healthbridge.marts build`")
    built_at, params, rows = conn.execute(
        "SELECT built_at, parameters, rows FROM marts.build_log WHERE run_id = %s", (run_id,)).fetchone()
    return {"run_id": run_id, "built_at": built_at, "parameters": params, "rows": rows,
            "n_countries": params["n_countries"], "year_min": params["year_min"],
            "year_max": params["year_max"]}


def indicators(conn: psycopg.Connection) -> pd.DataFrame:
    return _df(conn, "SELECT concept, name, unit, domain, definition FROM core.dim_indicator ORDER BY concept")


def countries(conn: psycopg.Connection) -> pd.DataFrame:
    return _df(conn, "SELECT trim(iso3) AS iso3, name AS country, un_subregion, who_region_code"
                     " FROM core.dim_country ORDER BY name")


def trust(conn: psycopg.Connection) -> pd.DataFrame:
    return _df(conn, """
        SELECT i.concept, i.name AS indicator_full, t.n_values, t.n_countries, t.year_min, t.year_max,
               t.grid_coverage, t.share_single_evidence, t.share_cross_validated, t.share_conflict,
               t.share_outlier_flag, t.share_source_disagreement
        FROM marts.data_trust t JOIN core.dim_indicator i USING (indicator_key)
        WHERE t.run_id = (SELECT run_id FROM marts.latest_run) ORDER BY i.concept""")


def panel(conn: psycopg.Connection, concept: str, iso3s: list[str] | None = None) -> pd.DataFrame:
    """Reconciled values for one indicator, optionally for a few countries."""
    sql = """SELECT iso3, country, un_subregion, year, value, quality_tier, outlier_flag,
                    source_disagreement, n_sources, n_evidence_groups, reconciled_source
             FROM marts.v_panel WHERE concept = %s"""
    params: list = [concept]
    if iso3s:
        sql += " AND trim(iso3) = ANY(%s)"
        params.append(iso3s)
    df = _df(conn, sql + " ORDER BY country, year", tuple(params))
    df["iso3"] = df["iso3"].str.strip()
    return df


def region_band(conn: psycopg.Connection, concept: str, un_subregion: str) -> pd.DataFrame:
    return _df(conn, """
        SELECT r.year, r.n_countries, r.n_possible, r.median_value, r.min_value, r.max_value
        FROM marts.region_year r JOIN core.dim_indicator i USING (indicator_key)
        WHERE r.run_id = (SELECT run_id FROM marts.latest_run) AND i.concept = %s
          AND r.un_subregion = %s ORDER BY r.year""", (concept, un_subregion))


def country_latest(conn: psycopg.Connection, iso3: str) -> pd.DataFrame:
    df = _df(conn, """SELECT concept, indicator, unit, latest_year, value, quality_tier, first_year,
                             n_observations, years_behind
                      FROM marts.v_country_latest WHERE trim(iso3) = %s ORDER BY concept""", (iso3,))
    return df


def country_trends(conn: psycopg.Connection, iso3: str) -> pd.DataFrame:
    return _df(conn, """
        SELECT i.concept, t.window_start, t.window_end, t.n_points, t.absolute_change,
               t.linear_slope_per_year, t.annual_pct_change
        FROM marts.trend t JOIN core.dim_indicator i USING (indicator_key)
        JOIN core.dim_country c USING (country_key)
        WHERE t.run_id = (SELECT run_id FROM marts.latest_run) AND trim(c.iso3) = %s
        ORDER BY i.concept""", (iso3,))


def association(conn: psycopg.Connection, concept_a: str, concept_b: str, basis: str) -> dict | None:
    row = conn.execute("""
        SELECT x.n_countries, x.spearman_rho, x.rho_ci_low, x.rho_ci_high, x.reference
        FROM marts.indicator_association x
        JOIN core.dim_indicator a ON a.indicator_key = x.indicator_a
        JOIN core.dim_indicator b ON b.indicator_key = x.indicator_b
        WHERE x.run_id = (SELECT run_id FROM marts.latest_run) AND x.basis = %s
          AND a.concept IN (%s, %s) AND b.concept IN (%s, %s) AND a.concept <> b.concept""",
                       (basis, concept_a, concept_b, concept_a, concept_b)).fetchone()
    if row is None:
        return None
    return {"n": row[0], "rho": row[1], "ci_low": row[2], "ci_high": row[3], "reference": row[4]}


def scatter(conn: psycopg.Connection, concept_a: str, concept_b: str, basis: str) -> pd.DataFrame:
    """One point per country, built with the same alignment rule as the association mart."""
    p = snapshot_info(conn)["parameters"]
    sql = """
        WITH near AS (
            SELECT iso3, country, un_subregion, concept, year, value,
                   row_number() OVER (PARTITION BY iso3, concept ORDER BY abs(year - %(ref)s), year) AS rn
            FROM marts.v_panel WHERE concept IN (%(a)s, %(b)s) AND quality_tier <> 'conflict'
              AND abs(year - %(ref)s) <= %(tol)s),
        near0 AS (
            SELECT iso3, concept, year, value,
                   row_number() OVER (PARTITION BY iso3, concept ORDER BY abs(year - %(base)s), year) AS rn
            FROM marts.v_panel WHERE concept IN (%(a)s, %(b)s) AND quality_tier <> 'conflict'
              AND abs(year - %(base)s) <= %(tol)s),
        level AS (
            SELECT iso3, country, un_subregion, concept, value FROM near WHERE rn = 1),
        change AS (
            SELECT n.iso3, n.country, n.un_subregion, n.concept, n.value - s.value AS value
            FROM near n JOIN near0 s ON s.iso3 = n.iso3 AND s.concept = n.concept AND s.rn = 1
            WHERE n.rn = 1 AND n.year - s.year >= %(span)s),
        chosen AS (SELECT * FROM level WHERE %(basis)s = 'level' UNION ALL
                   SELECT * FROM change WHERE %(basis)s = 'change')
        SELECT trim(a.iso3) AS iso3, a.country, a.un_subregion, a.value AS x, b.value AS y
        FROM chosen a JOIN chosen b ON a.iso3 = b.iso3 AND a.concept = %(a)s AND b.concept = %(b)s
        ORDER BY a.country"""
    return _df(conn, sql, {"a": concept_a, "b": concept_b, "basis": basis, "ref": p["ref_year"],
                           "base": p["base_year"], "tol": p["tolerance"],
                           "span": p["change_min_span"]})


def equity(conn: psycopg.Connection, concept: str, dimension: str) -> pd.DataFrame:
    """Each country's most recent gap for one indicator and dimension."""
    df = _df(conn, """
        SELECT DISTINCT ON (g.country_key) trim(c.iso3) AS iso3, c.name AS country, c.un_subregion,
               g.year, g.group_a, g.group_b, g.value_a, g.value_b, g.ratio, g.difference,
               g.source, g.n_candidate_pairs
        FROM marts.equity_gap g JOIN core.dim_country c USING (country_key)
        JOIN core.dim_indicator i USING (indicator_key)
        WHERE g.run_id = (SELECT run_id FROM marts.latest_run) AND i.concept = %s AND g.dimension = %s
        ORDER BY g.country_key, g.year DESC""", (concept, dimension))
    return df.sort_values("country").reset_index(drop=True)


def equity_options(conn: psycopg.Connection) -> pd.DataFrame:
    """Which (indicator, dimension) combinations have gaps."""
    return _df(conn, """
        SELECT i.concept, i.name AS indicator, g.dimension, count(DISTINCT g.country_key) AS countries
        FROM marts.equity_gap g JOIN core.dim_indicator i USING (indicator_key)
        WHERE g.run_id = (SELECT run_id FROM marts.latest_run)
        GROUP BY 1, 2, 3 ORDER BY 1, 3""")


def pipeline_summary(conn: psycopg.Connection) -> dict:
    """Pipeline accounting and the before/after data-quality numbers for the latest snapshot."""
    run_id = latest_run(conn)
    core = conn.execute(
        "SELECT staged_rows, rows_loaded, rows_rejected, headline_rows, selected_rows"
        " FROM core.build_log WHERE run_id = %s", (run_id,)).fetchone()
    out: dict = {"run_id": run_id, "accounting": None, "scores": None, "fanout": None}
    if core:
        out["accounting"] = dict(zip(("staged", "loaded", "rejected", "headline", "selected"), core,
                                     strict=True))
    runs = {stage: dq_id for stage, dq_id in conn.execute(
        "SELECT stage, max(dq_run_id) FROM dq.run WHERE snapshot_run_id = %s GROUP BY 1",
        (run_id,)).fetchall()}
    if {"staging", "core"} <= runs.keys():
        scores = _df(conn, """
            SELECT b.metric AS dimension, b.value AS before, a.value AS after
            FROM dq.metric b JOIN dq.metric a ON a.metric = b.metric AND a.scope = b.scope
             AND a.source = b.source AND a.concept = b.concept AND a.dimension = b.dimension
            WHERE b.dq_run_id = %s AND a.dq_run_id = %s AND b.scope = 'overall' AND b.dimension = 'score'""",
                     (runs["staging"], runs["core"]))
        order = {"completeness": 0, "validity": 1, "uniqueness": 2, "consistency": 3, "composite": 4}
        out["scores"] = scores.sort_values("dimension", key=lambda s: s.map(order)).reset_index(drop=True)
        out["fanout"] = _df(conn, """
            SELECT b.concept, b.value AS before, a.value AS after
            FROM dq.metric b JOIN dq.metric a ON a.metric = b.metric AND a.concept = b.concept
             AND a.scope = b.scope AND a.dimension = b.dimension
            WHERE b.dq_run_id = %s AND a.dq_run_id = %s AND b.scope = 'concept'
              AND b.metric = 'naive_join_fanout' ORDER BY b.concept""", (runs["staging"], runs["core"]))
    return out
