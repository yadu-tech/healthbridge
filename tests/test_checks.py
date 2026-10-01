"""Tests for the added quality checks: country resolution, temporal outliers, and source
disagreement inside an evidence group. Expected outcomes are worked out by hand."""
import json

import pytest

from healthbridge.core.build import build_core
from healthbridge.core.outliers import is_outlier
from healthbridge.ingest.snapshot import SnapshotWriter
from healthbridge.ingest.sources import RawPage
from healthbridge.reference import normalize_alias, read_csv
from healthbridge.staging.load import load_snapshot

U5 = "under5_mortality"


def who_row(iso, year, value):
    return {"Id": 1, "IndicatorCode": "X", "SpatialDimType": "COUNTRY", "SpatialDim": iso,
            "ParentLocationCode": "AFR", "ParentLocation": "Africa", "TimeDimType": "YEAR",
            "TimeDim": year, "Dim1Type": "SEX", "Dim1": "SEX_BTSX", "Dim3Type": "WEALTHQUINTILE",
            "Dim3": "WEALTHQUINTILE_TOTL", "Value": str(value), "NumericValue": value}


def wb_row(iso3, iso2, year, value):
    return {"indicator": {"id": "SH.X", "value": "x"}, "country": {"id": iso2, "value": iso3},
            "countryiso3code": iso3, "date": str(year), "value": value, "unit": "", "obs_status": "",
            "decimal": 0}


def snapshot(root, run_id, *, who_rows=(), wb_rows=(), years=(2010, 2021)):
    writer = SnapshotWriter(root, run_id=run_id, scope={"countries": ["NGA", "KEN", "GHA", "CIV"],
                                                        "years": list(years)})
    pages = []
    if who_rows:
        pages.append(RawPage("who", "WHO_U5", U5, 1, "u", 200, "application/json",
                             json.dumps({"value": list(who_rows)}).encode(), "json"))
    if wb_rows:
        pages.append(RawPage("worldbank", "SH.DYN.MORT", U5, 1, "u", 200, "application/json",
                             json.dumps([{"page": 1, "pages": 1}, list(wb_rows)]).encode(), "json"))
    writer.write_pages(pages)
    writer.finalize()
    return writer.run_dir


def q(conn, sql, *args):
    return conn.execute(sql, args).fetchall()


# --- country identifier resolution -------------------------------------------------------

@pytest.mark.integration
def test_recoverable_identifiers_are_resolved_and_the_method_is_recorded(pg_conn, tmp_path):
    rows = [who_row("nga", 2012, 100), who_row("NG", 2013, 98), who_row("Nigeria", 2014, 96),
            who_row("Ivory Coast", 2012, 80), who_row("XXX", 2012, 50), who_row("AFR", 2013, 50),
            who_row("NGA", 2015, 94)]
    run_dir = snapshot(tmp_path, "chk_country", who_rows=rows)
    load_snapshot(pg_conn, run_dir)
    build_core(pg_conn, run_dir)

    got = q(pg_conn, "SELECT c.iso3, f.year, f.country_resolution FROM core.fact_observation f"
                     " JOIN core.dim_country c USING (country_key) WHERE f.run_id = 'chk_country'"
                     " ORDER BY c.iso3, f.year")
    assert [(a.strip(), b, c) for a, b, c in got] == [
        ("CIV", 2012, "alias"), ("NGA", 2012, "normalized"), ("NGA", 2013, "iso2"),
        ("NGA", 2014, "alias"), ("NGA", 2015, "exact")]
    rejected = q(pg_conn, "SELECT reason, count(*) FROM core.rejected_record"
                          " WHERE run_id = 'chk_country' GROUP BY 1")
    assert rejected == [("unknown_country", 2)]  # an unknown code and a region code are not guessed


def test_sql_and_python_name_normalisation_agree(pg_conn):
    names = [r["alias"] for r in read_csv("country_aliases.csv")] + [
        r["name"] for r in read_csv("countries.csv")] + ["  Ghana ", "CÔTE D'IVOIRE", "São Tomé"]
    for name in names:
        sql_value = pg_conn.execute("SELECT core.normalize_name(%s)", (name,)).fetchone()[0]
        assert sql_value == normalize_alias(name), name


# --- temporal outliers -------------------------------------------------------------------

def test_outlier_rule_unit_cases():
    smooth = [96.0, 94.0, 88.0, 86.0]  # median 91, MAD 3
    assert is_outlier(500, smooth, 3.5, 0.2, 0.1, 3)
    assert not is_outlier(97, smooth, 3.5, 0.2, 0.1, 3)        # within the relative floor
    assert not is_outlier(500, [96.0, 94.0], 3.5, 0.2, 0.1, 3)  # too little context to judge
    assert not is_outlier(0.05, [0.0, 0.0, 0.0], 3.5, 0.2, 0.1, 3)  # absolute floor guards zeros


@pytest.mark.integration
def test_only_the_spike_in_a_smooth_series_is_flagged(pg_conn, tmp_path):
    values = [100, 98, 96, 94, 92, 500, 88, 86, 84, 82, 80]  # 2010..2020, spike in 2015
    rows = [wb_row("NGA", "NG", 2010 + i, v) for i, v in enumerate(values)]
    run_dir = snapshot(tmp_path, "chk_outlier", wb_rows=rows, years=(2010, 2020))
    load_snapshot(pg_conn, run_dir)
    build_core(pg_conn, run_dir)
    flagged = q(pg_conn, "SELECT year, value FROM core.fact_observation WHERE run_id = 'chk_outlier'"
                         " AND 'temporal_outlier' = ANY(quality_flags) ORDER BY year")
    assert flagged == [(2015, 500.0)]
    # flagged, not rejected: the row is still on the default surface
    assert q(pg_conn, "SELECT count(*) FROM core.v_headline_observation"
                      " WHERE run_id = 'chk_outlier'")[0][0] == 11


# --- source disagreement inside an evidence group ----------------------------------------

@pytest.mark.integration
def test_one_disagreeing_cell_in_a_dependent_pair_is_flagged(pg_conn, tmp_path):
    countries = [("NGA", "NG", 120), ("KEN", "KE", 60), ("GHA", "GH", 80)]
    who_rows, wb_rows = [], []
    for iso3, iso2, base in countries:
        for year in range(2010, 2017):
            value = base - 2 * (year - 2010)
            who_rows.append(who_row(iso3, year, value))
            # World Bank repeats WHO within 0.1%, except one cell that is 10% higher
            factor = 1.10 if (iso3, year) == ("KEN", 2013) else 1.001
            wb_rows.append(wb_row(iso3, iso2, year, round(value * factor, 3)))
    run_dir = snapshot(tmp_path, "chk_group", who_rows=who_rows, wb_rows=wb_rows, years=(2010, 2016))
    load_snapshot(pg_conn, run_dir)
    build_core(pg_conn, run_dir, dependence_min_shared=1)

    groups = q(pg_conn, "SELECT DISTINCT group_label FROM core.evidence_group WHERE run_id = 'chk_group'")
    assert groups == [("who+worldbank",)]  # 20 of 21 cells agree within 1%: dependent
    flagged = q(pg_conn, "SELECT c.iso3, r.year, round(r.max_within_group_spread_rel::numeric, 3), r.conflict"
                         " FROM core.fact_reconciled r JOIN core.dim_country c USING (country_key)"
                         " WHERE r.run_id = 'chk_group' AND r.within_group_disagreement")
    assert [(a.strip(), b, float(c), d) for a, b, c, d in flagged] == [("KEN", 2013, 0.095, False)]
    total = q(pg_conn, "SELECT count(*) FROM core.fact_reconciled WHERE run_id = 'chk_group'")[0][0]
    assert total == 21


@pytest.mark.integration
def test_a_corrupted_country_code_is_rejected_per_row_and_does_not_stop_the_build(pg_conn, tmp_path):
    bad = wb_row("XXX", "NG", 2012, 50)
    bad["country"]["value"] = "Nigeria"          # the name belongs to a real country
    good = [wb_row("NGA", "NG", 2010 + i, 100 - i) for i in range(4)]
    run_dir = snapshot(tmp_path, "chk_badcode", wb_rows=[bad, *good], years=(2010, 2020))
    load_snapshot(pg_conn, run_dir)
    summary = build_core(pg_conn, run_dir)            # must not raise ReferenceMismatch
    assert summary["rows_rejected"] == 1 and summary["rows_loaded"] == 4
    reason = q(pg_conn, "SELECT reason FROM core.rejected_record WHERE run_id = 'chk_badcode'")
    assert reason == [("unknown_country",)]
    # the corrupted row must not have taught the reference a wrong alias
    assert q(pg_conn, "SELECT iso3 FROM core.country_alias WHERE alias_norm = 'nigeria'")[0][0].strip() == "NGA"


@pytest.mark.integration
def test_one_unknown_code_in_two_who_regions_does_not_stop_the_build(pg_conn, tmp_path):
    rows = [who_row("ABC", 2012, 50), who_row("ABC", 2013, 49), who_row("NGA", 2012, 100)]
    rows[1]["ParentLocationCode"] = "EMR"        # the same unknown code, seen under two regions
    run_dir = snapshot(tmp_path, "chk_region", who_rows=rows)
    load_snapshot(pg_conn, run_dir)
    summary = build_core(pg_conn, run_dir)       # must not raise ReferenceMismatch
    assert (summary["rows_loaded"], summary["rows_rejected"]) == (1, 2)
    assert q(pg_conn, "SELECT DISTINCT reason FROM core.rejected_record WHERE run_id = 'chk_region'") == [
        ("unknown_country",)]
