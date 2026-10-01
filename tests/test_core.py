"""Core layer tests on a hand-made dataset whose expected outcome is worked out row by row.

Scope: countries NGA, KEN, GHA; years 2019-2021. Two concepts: under5_mortality (sources agree,
so they form one evidence group) and stunting_prevalence (WHO disagrees with the survey-based
UNICEF/World Bank values, and UNICEF has two surveys for one country-year).
"""
import json

import pytest

from healthbridge.core.build import build_core
from healthbridge.dq.baseline import compute_baseline
from healthbridge.ingest.snapshot import SnapshotWriter
from healthbridge.ingest.sources import RawPage
from healthbridge.reference import ReferenceMismatch, normalize_alias, read_csv
from healthbridge.staging.load import load_snapshot

U5, STUNT = "under5_mortality", "stunting_prevalence"


def who(iso, year, value, *, dim1="SEX_BTSX", dim3="WEALTHQUINTILE_TOTL", region="AFR"):
    return {"Id": 1, "IndicatorCode": "X", "SpatialDimType": "COUNTRY", "SpatialDim": iso,
            "ParentLocationCode": region, "ParentLocation": "Region", "TimeDimType": "YEAR",
            "TimeDim": year, "Dim1Type": "SEX", "Dim1": dim1,
            "Dim3Type": "WEALTHQUINTILE" if dim3 else None, "Dim3": dim3,
            "Value": str(value), "NumericValue": value}


def wb(iso, iso2, year, value):
    return {"indicator": {"id": "SH.X", "value": "x"}, "country": {"id": iso2, "value": iso},
            "countryiso3code": iso, "date": str(year), "value": value, "unit": "", "obs_status": "",
            "decimal": 0}


# under5_mortality, WHO (8 rows):
#  a NGA 2019 total 100 (headline)      b NGA 2019 male 110 (not headline)
#  c NGA 2019 quintile 1 130 (not headline)   d KEN 2019 total 50 (headline)
#  e XXX unknown country -> rejected    f GHA 2020 value 5000 out of range -> rejected
#  g GHA 2021 sex code SEX_WEIRD -> rejected as unmapped   h exact duplicate of a -> rejected
WHO_U5 = [
    who("NGA", 2019, 100), who("NGA", 2019, 110, dim1="SEX_MLE"),
    who("NGA", 2019, 130, dim3="WEALTHQUINTILE_WQ1"), who("KEN", 2019, 50),
    who("XXX", 2019, 50, region="EMR"), who("GHA", 2020, 5000),
    who("GHA", 2021, 40, dim1="SEX_WEIRD"), who("NGA", 2019, 100),
]
WB_U5 = [wb("NGA", "NG", 2019, 100.4), wb("KEN", "KE", 2019, 50.2), wb("GHA", "GH", 2019, None)]
UNICEF_U5 = (
    "REF_AREA,INDICATOR,SEX,WEALTH_QUINTILE,DATA_SOURCE,TIME_PERIOD,OBS_VALUE\n"
    "NGA,CME_MRY0T4,_T,_T,UN_IGME,2019,100.1\n"
    "NGA,CME_MRY0T4,F,_T,UN_IGME,2019,95\n"
    "KEN,CME_MRY0T4,_T,_T,UN_IGME,2019,50.1\n"
)
# stunting: WHO says 40 for KEN 2019; UNICEF has two surveys (priority 1 -> 30, priority 0 -> 35);
# World Bank 30.3 agrees with the UNICEF survey. NGA has a headline row (age _T, 25) and a
# 0-23 months breakdown (20); GHA only has a male breakdown (22).
WHO_STUNT = [{**who("KEN", 2019, 40), "Dim3Type": None, "Dim3": None}]
WB_STUNT = [wb("KEN", "KE", 2019, 30.3)]
HEADER = ("REF_AREA,INDICATOR,SEX,AGE,WEALTH_QUINTILE,RESIDENCE,MATERNAL_EDU_LVL,HEAD_OF_HOUSE,"
          "DATA_SOURCE,DATA_SOURCE_PRIORITY,TIME_PERIOD,OBS_VALUE\n")
UNICEF_STUNT = HEADER + (
    "KEN,NT_ANT_HAZ_NE2,_T,_T,_T,_T,_T,_T,Survey B,1,2019-10,30\n"
    "KEN,NT_ANT_HAZ_NE2,_T,_T,_T,_T,_T,_T,Survey A,0,2019-06,35\n"
    "NGA,NT_ANT_HAZ_NE2,_T,_T,_T,_T,_T,_T,Survey C,1,2019-03,25\n"
    "NGA,NT_ANT_HAZ_NE2,_T,M0T23,_T,_T,_T,_T,Survey C,1,2019-03,20\n"
    "GHA,NT_ANT_HAZ_NE2,M,_T,_T,_T,_T,_T,Survey D,1,2019-05,22\n"
)


def build_snapshot(root, run_id, wb_u5=WB_U5):
    writer = SnapshotWriter(root, run_id=run_id,
                            scope={"countries": ["NGA", "KEN", "GHA"], "years": [2019, 2021]})

    def js(payload):
        return json.dumps(payload).encode()

    def page(source, series, concept, content, ext="json", ctype="application/json"):
        return RawPage(source, series, concept, 1, "u", 200, ctype, content, ext)

    writer.write_pages([
        page("who", "WHO_U5", U5, js({"value": WHO_U5})),
        page("worldbank", "SH.DYN.MORT", U5, js([{"page": 1, "pages": 1}, wb_u5])),
        page("unicef", "CME.CME_MRY0T4", U5, UNICEF_U5.encode(), "csv", "text/csv"),
        page("who", "WHO_ST", STUNT, js({"value": WHO_STUNT})),
        page("worldbank", "SH.STA.STNT.ZS", STUNT, js([{"page": 1, "pages": 1}, WB_STUNT])),
        page("unicef", "NUTRITION.NT_ANT_HAZ_NE2", STUNT, UNICEF_STUNT.encode(), "csv", "text/csv"),
    ])
    writer.finalize()
    return writer.run_dir


@pytest.fixture
def core_run(pg_conn, tmp_path):
    run_dir = build_snapshot(tmp_path, "core1")
    load_snapshot(pg_conn, run_dir)
    summary = build_core(pg_conn, run_dir, dependence_min_shared=1)
    return pg_conn, run_dir, summary


def rows(conn, sql, *args):
    return conn.execute(sql, args).fetchall()


@pytest.mark.integration
def test_pipeline_accounting_matches_the_hand_count(core_run):
    _, _, summary = core_run
    assert summary == {"run_id": "core1", "staged_rows": 21, "rows_loaded": 16, "rows_rejected": 5,
                       "headline_rows": 11, "selected_rows": 10}


@pytest.mark.integration
def test_every_rejection_has_the_expected_reason(core_run):
    conn, _, _ = core_run
    got = rows(conn, "SELECT source, reason, count(*) FROM core.rejected_record"
                     " WHERE run_id = 'core1' GROUP BY 1, 2 ORDER BY 1, 2")
    assert got == [("who", "exact_duplicate", 1), ("who", "out_of_range", 1),
                   ("who", "unknown_country", 1), ("who", "unmapped_vocabulary", 1),
                   ("worldbank", "no_value", 1)]


@pytest.mark.integration
def test_vocabulary_is_canonical_and_breakdowns_are_not_headline(core_run):
    conn, _, _ = core_run
    got = rows(conn, """
        SELECT f.value, f.sex, f.wealth_quintile, f.is_headline FROM core.fact_observation f
        JOIN core.dim_source s USING (source_key) JOIN core.dim_country c USING (country_key)
        JOIN core.dim_indicator i USING (indicator_key)
        WHERE f.run_id = 'core1' AND s.source_code = 'who' AND i.concept = 'under5_mortality'
          AND c.iso3 = 'NGA' ORDER BY f.value""")
    assert got == [(100.0, "total", "total", True), (110.0, "male", "total", False),
                   (130.0, "total", "q1", False)]


@pytest.mark.integration
def test_unicef_age_defines_the_headline_population(core_run):
    conn, _, _ = core_run
    got = rows(conn, """
        SELECT f.value, f.age_group, f.is_headline, f.is_selected FROM core.fact_observation f
        JOIN core.dim_country c USING (country_key) JOIN core.dim_source s USING (source_key)
        JOIN core.dim_indicator i USING (indicator_key)
        WHERE f.run_id = 'core1' AND s.source_code = 'unicef' AND i.concept = 'stunting_prevalence'
          AND c.iso3 = 'NGA' ORDER BY f.value""")
    assert got == [(20.0, "M0T23", False, False), (25.0, "_T", True, True)]


@pytest.mark.integration
def test_two_surveys_for_one_cell_select_the_producers_priority_and_keep_both(core_run):
    conn, _, _ = core_run
    got = rows(conn, """
        SELECT f.value, f.upstream_label, f.is_selected, f.n_candidates, f.selection_rule
        FROM core.fact_observation f JOIN core.dim_country c USING (country_key)
        JOIN core.dim_source s USING (source_key) JOIN core.dim_indicator i USING (indicator_key)
        WHERE f.run_id = 'core1' AND s.source_code = 'unicef' AND i.concept = 'stunting_prevalence'
          AND c.iso3 = 'KEN' ORDER BY f.value""")
    rule = "source_priority_then_latest_period"
    assert got == [(30.0, "Survey B", True, 2, rule), (35.0, "Survey A", False, 2, rule)]


@pytest.mark.integration
def test_default_surface_has_one_row_per_source_country_indicator_year(core_run):
    conn, _, _ = core_run
    dupes = rows(conn, "SELECT count(*) FROM (SELECT 1 FROM core.v_headline_observation"
                       " WHERE run_id = 'core1' GROUP BY source, concept, iso3, year"
                       " HAVING count(*) > 1) x")[0][0]
    total = rows(conn, "SELECT count(*) FROM core.v_headline_observation WHERE run_id = 'core1'")[0][0]
    assert (dupes, total) == (0, 10)


@pytest.mark.integration
def test_source_dependence_evidence_groups_and_reconciliation(core_run):
    conn, _, _ = core_run
    groups = rows(conn, "SELECT concept, group_label FROM core.evidence_group WHERE run_id = 'core1'"
                        " GROUP BY 1, 2 ORDER BY 1, 2")
    assert groups == [("stunting_prevalence", "unicef+worldbank"), ("stunting_prevalence", "who"),
                      ("under5_mortality", "unicef+who+worldbank")]
    dep = rows(conn, "SELECT source_a, source_b, dependent FROM core.source_dependence"
                     " WHERE run_id = 'core1' AND concept = 'stunting_prevalence' ORDER BY 1, 2")
    # pairs are stored in source_key order (who, unicef, worldbank)
    assert dep == [("unicef", "worldbank", True), ("who", "unicef", False), ("who", "worldbank", False)]

    cells = rows(conn, """
        SELECT i.concept, c.iso3, r.n_sources, r.n_evidence_groups, r.reconciled_value,
               s.source_code, r.conflict FROM core.fact_reconciled r
        JOIN core.dim_indicator i USING (indicator_key) JOIN core.dim_country c USING (country_key)
        JOIN core.dim_source s ON s.source_key = r.reconciled_source_key
        WHERE r.run_id = 'core1' ORDER BY 1, 2""")
    assert cells == [
        ("stunting_prevalence", "KEN", 3, 2, 40.0, "who", True),     # independent groups disagree
        ("stunting_prevalence", "NGA", 1, 1, 25.0, "unicef", False),
        ("under5_mortality", "KEN", 3, 1, 50.0, "who", False),       # one group: no cross-check
        ("under5_mortality", "NGA", 3, 1, 100.0, "who", False),
    ]
    spread = rows(conn, "SELECT round(spread_rel::numeric, 4) FROM core.fact_reconciled r"
                        " JOIN core.dim_country c USING (country_key) JOIN core.dim_indicator i"
                        " USING (indicator_key) WHERE run_id = 'core1' AND c.iso3 = 'KEN'"
                        " AND i.concept = 'stunting_prevalence'")
    assert float(spread[0][0]) == pytest.approx(10 / 35, abs=1e-4)


@pytest.mark.integration
def test_rebuild_is_idempotent(core_run):
    conn, run_dir, first = core_run
    second = build_core(conn, run_dir, dependence_min_shared=1)
    assert second == first
    assert rows(conn, "SELECT count(*) FROM core.fact_observation WHERE run_id = 'core1'")[0][0] == 16
    assert rows(conn, "SELECT count(*) FROM core.rejected_record WHERE run_id = 'core1'")[0][0] == 5


@pytest.mark.integration
def test_every_core_row_traces_back_to_its_staging_row(core_run):
    conn, _, _ = core_run
    bad = rows(conn, """
        SELECT count(*) FROM core.v_headline_observation h
        WHERE h.run_id = 'core1' AND NOT EXISTS (
            SELECT 1 FROM staging.v_observation v
            WHERE v.run_id = h.run_id AND v.source_file = h.staging_source_file
              AND v.row_num = h.staging_row_num AND v.value = h.value)""")[0][0]
    assert bad == 0


@pytest.mark.integration
def test_iso2_disagreement_between_reference_and_source_is_an_error(pg_conn, tmp_path):
    bad_wb = [wb("NGA", "XX", 2019, 100.4)]  # reference says Nigeria is NG
    run_dir = build_snapshot(tmp_path, "core_bad", wb_u5=bad_wb)
    load_snapshot(pg_conn, run_dir)
    with pytest.raises(ReferenceMismatch, match="ISO2 mismatch for NGA"):
        build_core(pg_conn, run_dir)
    assert rows(pg_conn, "SELECT count(*) FROM core.build_log WHERE run_id = 'core_bad'")[0][0] == 0


@pytest.mark.integration
def test_unloaded_snapshot_is_refused(pg_conn, tmp_path):
    run_dir = build_snapshot(tmp_path, "core_unloaded")
    with pytest.raises(RuntimeError, match="not loaded"):
        build_core(pg_conn, run_dir)


@pytest.mark.integration
def test_reference_dimensions_are_complete_and_consistent(core_run):
    conn, _, _ = core_run
    assert rows(conn, "SELECT count(*), count(DISTINCT iso2) FROM core.dim_country")[0] == (54, 54)
    assert rows(conn, "SELECT count(*) FROM core.indicator_source_map")[0][0] == 21  # 7 concepts x 3
    assert rows(conn, "SELECT iso3 FROM core.country_alias WHERE alias_norm = %s",
                normalize_alias("Ivory Coast"))[0][0].strip() == "CIV"
    assert rows(conn, "SELECT who_region_code FROM core.dim_country WHERE iso3 = 'NGA'")[0][0] == "AFR"
    assert rows(conn, "SELECT count(*) FROM core.dim_country WHERE un_subregion IS NULL")[0][0] == 0


def test_alias_normalisation_folds_accents_case_and_punctuation():
    assert normalize_alias("Côte d'Ivoire") == normalize_alias("COTE DIVOIRE") == "cotedivoire"
    assert normalize_alias("Gambia, The") == "gambiathe"


def test_reference_files_cover_exactly_the_54_states_and_7_concepts():
    countries = read_csv("countries.csv")
    assert len(countries) == 54 and len({c["iso3"] for c in countries}) == 54
    assert len(read_csv("indicators.csv")) == 7
    sub = {c["un_subregion"] for c in countries}
    assert sub == {"Northern Africa", "Western Africa", "Middle Africa", "Eastern Africa",
                   "Southern Africa"}


@pytest.mark.integration
def test_core_layer_is_measurable_with_the_same_engine(core_run):
    conn, run_dir, _ = core_run
    dq_id = compute_baseline(conn, run_dir, stage="core")
    m = {(r[0], r[1], r[2], r[3], r[4]): r for r in conn.execute(
        "SELECT scope, source, concept, dimension, metric, value, numerator, denominator"
        " FROM dq.metric WHERE dq_run_id = %s", (dq_id,)).fetchall()}
    staged = m[("series", "who", U5, "lineage", "records_staged")]
    assert staged[5] == 8
    rejected = m[("series", "who", U5, "lineage", "records_rejected")]
    assert (rejected[6], rejected[7]) == (4, 8)
    fan = m[("concept", "", U5, "integration", "naive_join_fanout")]
    assert fan[5] == 1.0  # one row per source per country-year after the pipeline
    assert m[("overall", "", "", "score", "uniqueness")][5] == 1.0
