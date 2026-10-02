"""Mart tests on a hand-built dataset. Expected values come from an independent Python
implementation (separate Spearman, exact growth rates), not from the SQL under test."""
import json
import math

import pytest

from healthbridge.core.build import build_core
from healthbridge.ingest.snapshot import SnapshotWriter
from healthbridge.ingest.sources import RawPage
from healthbridge.marts.build import build_marts
from healthbridge.staging.load import load_snapshot

U5, DTP3, STUNT = "under5_mortality", "dtp3_coverage", "stunting_prevalence"
YEARS = list(range(2000, 2017))
# under-5 mortality falls 3% a year from a country-specific base; DTP3 rises linearly
U5_BASE = {"NGA": 200, "MLI": 160, "UGA": 140, "KEN": 120, "GHA": 100, "SEN": 90}
DTP3_LINE = {"NGA": (30, 1.0), "KEN": (70, 1.5), "GHA": (60, 0.5), "SEN": (80, 0.8),
             "MLI": (40, 2.0), "UGA": (50, 1.2)}
WEST, EAST = ["NGA", "GHA", "SEN", "MLI"], ["KEN", "UGA"]


def who(iso, year, value, *, dim1=None, dim3=None):
    return {"Id": 1, "IndicatorCode": "X", "SpatialDimType": "COUNTRY", "SpatialDim": iso,
            "ParentLocationCode": "AFR", "ParentLocation": "Africa", "TimeDimType": "YEAR",
            "TimeDim": year, "Dim1Type": "SEX" if dim1 else None, "Dim1": dim1,
            "Dim3Type": "WEALTHQUINTILE" if dim3 else None, "Dim3": dim3,
            "Value": str(value), "NumericValue": value}


def wb(iso, iso2, year, value):
    return {"indicator": {"id": "SH.X", "value": "x"}, "country": {"id": iso2, "value": iso},
            "countryiso3code": iso, "date": str(year), "value": value, "unit": "", "obs_status": "",
            "decimal": 0}


def u5(country, year):
    return U5_BASE[country] * 0.97 ** (year - 2000)


def dtp3(country, year):
    start, slope = DTP3_LINE[country]
    return start + slope * (year - 2000)


def build_dataset():
    who_u5 = [who(c, y, u5(c, y), dim1="SEX_BTSX", dim3="WEALTHQUINTILE_TOTL")
              for c in U5_BASE for y in YEARS]
    # equity breakdowns for NGA 2010: wealth (poorest 240 vs richest 120) and sex (190 vs 210)
    who_u5 += [who("NGA", 2010, 240, dim1="SEX_BTSX", dim3="WEALTHQUINTILE_WQ1"),
               who("NGA", 2010, 120, dim1="SEX_BTSX", dim3="WEALTHQUINTILE_WQ5"),
               who("NGA", 2010, 190, dim1="SEX_FMLE", dim3="WEALTHQUINTILE_TOTL"),
               who("NGA", 2010, 210, dim1="SEX_MLE", dim3="WEALTHQUINTILE_TOTL")]
    who_dtp3 = [who(c, y, dtp3(c, y)) for c in DTP3_LINE for y in YEARS]
    # Morocco: a smooth series with one spike in 2008 (flagged as a temporal outlier)
    who_dtp3 += [who("MAR", y, 95 if y == 2008 else 50 + (y - 2000)) for y in YEARS]
    # stunting: WHO says 40 for KEN 2012 but the surveys say 30 (independent evidence disagrees);
    # NGA 2012: WHO 31 vs surveys 30 (independent but agreeing)
    who_st = [who("KEN", 2012, 40, dim1="SEX_BTSX"), who("NGA", 2012, 31, dim1="SEX_BTSX")]
    wb_st = [wb("KEN", "KE", 2012, 30.1), wb("NGA", "NG", 2012, 30.1)]
    header = ("REF_AREA,INDICATOR,SEX,AGE,WEALTH_QUINTILE,RESIDENCE,MATERNAL_EDU_LVL,HEAD_OF_HOUSE,"
              "DATA_SOURCE,DATA_SOURCE_PRIORITY,TIME_PERIOD,OBS_VALUE\n")

    def line(iso, res, source, prio, period, value):
        return f"{iso},NT_ANT_HAZ_NE2,_T,_T,_T,{res},_T,_T,{source},{prio},{period},{value}\n"

    uni_st = header + (
        line("KEN", "_T", "Survey A", 1, "2012-06", 30) + line("NGA", "_T", "Survey C", 1, "2012-05", 30)
        # two surveys in KEN 2014, each with urban and rural values; gaps must stay within a survey
        + line("KEN", "U", "Survey A", 1, "2014-06", 20) + line("KEN", "R", "Survey A", 1, "2014-06", 30)
        + line("KEN", "U", "Survey B", 1, "2014-10", 22) + line("KEN", "R", "Survey B", 1, "2014-10", 44))
    return who_u5, who_dtp3, who_st, wb_st, uni_st


def snapshot(root, run_id="mart1"):
    who_u5, who_dtp3, who_st, wb_st, uni_st = build_dataset()
    writer = SnapshotWriter(root, run_id=run_id, scope={
        "countries": ["NGA", "MLI", "UGA", "KEN", "GHA", "SEN", "MAR"], "years": [2000, 2016]})

    def js(payload):
        return json.dumps(payload).encode()

    def page(source, series, concept, content, ext="json", ctype="application/json"):
        return RawPage(source, series, concept, 1, "u", 200, ctype, content, ext)

    writer.write_pages([
        page("who", "WHO_U5", U5, js({"value": who_u5})),
        page("who", "WHO_DTP3", DTP3, js({"value": who_dtp3})),
        page("who", "WHO_ST", STUNT, js({"value": who_st})),
        page("worldbank", "SH.STA.STNT.ZS", STUNT, js([{"page": 1, "pages": 1}, wb_st])),
        page("unicef", "NUTRITION.NT_ANT_HAZ_NE2", STUNT, uni_st.encode(), "csv", "text/csv"),
    ])
    writer.finalize()
    return writer.run_dir


def ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    out, i = [0.0] * len(values), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def pearson(x, y):
    mx, my = sum(x) / len(x), sum(y) / len(y)
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True))
    return cov / math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))


def spearman(x, y):
    return pearson(ranks(x), ranks(y))


@pytest.fixture
def marts(pg_conn, tmp_path):
    run_dir = snapshot(tmp_path)
    load_snapshot(pg_conn, run_dir)
    build_core(pg_conn, run_dir, dependence_min_shared=1)
    summary = build_marts(pg_conn, run_dir, min_countries=5)
    return pg_conn, run_dir, summary


def q(conn, sql, *args):
    return conn.execute(sql, args).fetchall()


@pytest.mark.integration
def test_panel_rows_tiers_and_flags(marts):
    conn, _, summary = marts
    assert summary["country_indicator_year"] == 6 * 17 * 2 + 17 + 2   # u5, dtp3, Morocco, 2 stunting
    tiers = dict(q(conn, "SELECT quality_tier, count(*) FROM marts.country_indicator_year"
                         " WHERE run_id = 'mart1' GROUP BY 1"))
    assert tiers == {"single_evidence_group": 6 * 17 * 2 + 17, "conflict": 1, "cross_validated": 1}
    stunting = q(conn, "SELECT c.iso3, p.quality_tier, p.value, p.reconciled_source, p.n_evidence_groups"
                       " FROM marts.country_indicator_year p JOIN core.dim_country c USING (country_key)"
                       " JOIN core.dim_indicator i USING (indicator_key)"
                       " WHERE p.run_id = 'mart1' AND i.concept = 'stunting_prevalence' ORDER BY 1")
    assert [(a.strip(), b, c, d, e) for a, b, c, d, e in stunting] == [
        ("KEN", "conflict", 40.0, "who", 2), ("NGA", "cross_validated", 31.0, "who", 2)]
    flagged = q(conn, "SELECT c.iso3, p.year FROM marts.country_indicator_year p"
                      " JOIN core.dim_country c USING (country_key)"
                      " WHERE p.run_id = 'mart1' AND p.outlier_flag")
    assert [(a.strip(), b) for a, b in flagged] == [("MAR", 2008)]   # only the spike


@pytest.mark.integration
def test_country_latest(marts):
    conn, _, _ = marts
    row = q(conn, "SELECT latest_year, round(value::numeric, 6), first_year, n_observations, years_behind"
                  " FROM marts.country_latest l JOIN core.dim_country c USING (country_key)"
                  " JOIN core.dim_indicator i USING (indicator_key)"
                  " WHERE l.run_id = 'mart1' AND i.concept = 'under5_mortality' AND c.iso3 = 'NGA'")[0]
    assert row[0] == 2016 and row[2] == 2000 and row[3] == 17 and row[4] == 0
    assert float(row[1]) == pytest.approx(round(u5("NGA", 2016), 6))
    ken = q(conn, "SELECT latest_year, n_observations FROM marts.country_latest l"
                  " JOIN core.dim_country c USING (country_key) JOIN core.dim_indicator i USING (indicator_key)"
                  " WHERE l.run_id = 'mart1' AND i.concept = 'stunting_prevalence' AND c.iso3 = 'KEN'")[0]
    assert ken == (2012, 1)


@pytest.mark.integration
def test_trend_recovers_known_rates(marts):
    conn, _, _ = marts
    rows = q(conn, "SELECT c.iso3, t.n_points, t.window_start, t.window_end, t.annual_pct_change"
                   " FROM marts.trend t JOIN core.dim_country c USING (country_key)"
                   " JOIN core.dim_indicator i USING (indicator_key) WHERE t.run_id = 'mart1'"
                   " AND i.concept = 'under5_mortality'")
    assert len(rows) == 6
    for iso3, n, start, end, pct in rows:
        assert (n, start, end) == (17, 2000, 2016)
        assert pct == pytest.approx(0.97 - 1, abs=1e-9)      # exactly -3% a year
    lin = {c.strip(): (s, r2, change) for c, s, r2, change in q(
        conn, "SELECT c.iso3, t.linear_slope_per_year, t.linear_r2, t.absolute_change FROM marts.trend t"
              " JOIN core.dim_country c USING (country_key) JOIN core.dim_indicator i USING (indicator_key)"
              " WHERE t.run_id = 'mart1' AND i.concept = 'dtp3_coverage' AND c.iso3 <> 'MAR'")}
    for country, (_, slope) in DTP3_LINE.items():
        assert lin[country][0] == pytest.approx(slope, abs=1e-9)
        assert lin[country][1] == pytest.approx(1.0, abs=1e-9)
        assert lin[country][2] == pytest.approx(slope * 16, abs=1e-9)
    # a series with too few points has no trend
    assert q(conn, "SELECT count(*) FROM marts.trend t JOIN core.dim_indicator i USING (indicator_key)"
                   " WHERE t.run_id = 'mart1' AND i.concept = 'stunting_prevalence'")[0][0] == 0


@pytest.mark.integration
def test_region_summary(marts):
    conn, _, _ = marts
    west = [dtp3(c, 2010) for c in WEST]
    row = q(conn, "SELECT r.n_countries, r.n_possible, r.coverage_share, r.mean_value, r.median_value,"
                  " r.min_value, r.max_value FROM marts.region_year r JOIN core.dim_indicator i USING"
                  " (indicator_key) WHERE r.run_id = 'mart1' AND i.concept = 'dtp3_coverage'"
                  " AND r.un_subregion = 'Western Africa' AND r.year = 2010")[0]
    assert row[0] == 4 and row[1] == 16 and row[2] == pytest.approx(4 / 16)
    sorted_west = sorted(west)
    assert row[3] == pytest.approx(sum(west) / 4)
    assert row[4] == pytest.approx((sorted_west[1] + sorted_west[2]) / 2)
    assert (row[5], row[6]) == (pytest.approx(min(west)), pytest.approx(max(west)))
    east = q(conn, "SELECT r.n_countries, r.n_possible, r.mean_value FROM marts.region_year r"
                   " JOIN core.dim_indicator i USING (indicator_key) WHERE r.run_id = 'mart1'"
                   " AND i.concept = 'dtp3_coverage' AND r.un_subregion = 'Eastern Africa' AND r.year = 2010")[0]
    assert east[:2] == (2, 18) and east[2] == pytest.approx(sum(dtp3(c, 2010) for c in EAST) / 2)


@pytest.mark.integration
def test_associations_match_an_independent_spearman(marts):
    conn, _, _ = marts
    countries = list(U5_BASE)
    expected = {
        "level": spearman([u5(c, 2015) for c in countries], [dtp3(c, 2015) for c in countries]),
        "change": spearman([u5(c, 2015) - u5(c, 2000) for c in countries],
                           [dtp3(c, 2015) - dtp3(c, 2000) for c in countries]),
    }
    for basis, rho in expected.items():
        row = q(conn, "SELECT x.n_countries, x.spearman_rho, x.rho_ci_low, x.rho_ci_high, x.reference"
                      " FROM marts.indicator_association x JOIN core.dim_indicator a ON a.indicator_key = x.indicator_a"
                      " JOIN core.dim_indicator b ON b.indicator_key = x.indicator_b WHERE x.run_id = 'mart1'"
                      " AND x.basis = %s AND a.concept IN ('under5_mortality', 'dtp3_coverage')"
                      " AND b.concept IN ('under5_mortality', 'dtp3_coverage')", basis)[0]
        assert row[0] == 6                                    # Morocco has no under-5 series
        assert row[1] == pytest.approx(rho, abs=1e-9)
        half = 1.96 / math.sqrt(6 - 3)
        assert row[2] == pytest.approx(math.tanh(math.atanh(rho) - half), abs=1e-9)
        assert row[3] == pytest.approx(math.tanh(math.atanh(rho) + half), abs=1e-9)
    assert q(conn, "SELECT reference FROM marts.indicator_association WHERE run_id = 'mart1'"
                   " AND basis = 'level' LIMIT 1")[0][0] == "2015 +-3y"


@pytest.mark.integration
def test_association_needs_enough_countries(pg_conn, marts):
    conn, run_dir, _ = marts
    build_marts(conn, run_dir, min_countries=7)               # only 6 countries have both series
    assert q(conn, "SELECT count(*) FROM marts.indicator_association WHERE run_id = 'mart1'")[0][0] == 0


@pytest.mark.integration
def test_equity_gaps_pair_groups_within_a_survey(marts):
    conn, _, _ = marts
    rows = {(d, c.strip(), y): (ga, gb, va, vb, ratio, diff, src, n) for d, c, y, ga, gb, va, vb, ratio, diff, src, n in q(
        conn, "SELECT g.dimension, c.iso3, g.year, g.group_a, g.group_b, g.value_a, g.value_b, g.ratio,"
              " g.difference, g.source, g.n_candidate_pairs FROM marts.equity_gap g"
              " JOIN core.dim_country c USING (country_key) WHERE g.run_id = 'mart1'")}
    wealth = rows[("wealth", "NGA", 2010)]
    assert wealth == ("poorest", "richest", 240.0, 120.0, 2.0, 120.0, "who", 1)
    sex = rows[("sex", "NGA", 2010)]
    assert sex[:4] == ("female", "male", 190.0, 210.0)
    assert sex[4] == pytest.approx(190 / 210) and sex[5] == pytest.approx(-20.0)
    # two surveys in KEN 2014: the later one is chosen, and urban/rural never mix across surveys
    res = rows[("residence", "KEN", 2014)]
    assert res[:4] == ("rural", "urban", 44.0, 22.0)          # Survey B (2014-10), not A or a mix
    assert res[4] == pytest.approx(2.0) and res[7] == 2        # two candidate pairs were available
    assert not any(k[0] == "wealth" and k[1] == "KEN" for k in rows)   # no wealth data, no gap


@pytest.mark.integration
def test_data_trust(marts):
    conn, _, _ = marts
    got = {i: t for i, *t in q(
        conn, "SELECT i.concept, t.n_values, t.n_countries, t.grid_coverage, t.share_single_evidence,"
              " t.share_conflict, t.share_cross_validated, t.share_outlier_flag FROM marts.data_trust t"
              " JOIN core.dim_indicator i USING (indicator_key) WHERE t.run_id = 'mart1'")}
    n_dtp3 = 6 * 17 + 17
    assert got[DTP3][:2] == [n_dtp3, 7]
    assert got[DTP3][2] == pytest.approx(n_dtp3 / (54 * 17))     # 54 reference countries x 17 years
    assert got[DTP3][3] == pytest.approx(1.0) and got[DTP3][6] == pytest.approx(1 / n_dtp3)
    assert got[STUNT][0] == 2
    assert (got[STUNT][4], got[STUNT][5]) == (pytest.approx(0.5), pytest.approx(0.5))


@pytest.mark.integration
def test_views_follow_the_latest_build_and_carry_dimensions(marts):
    conn, _, _ = marts
    row = q(conn, "SELECT iso3, country, un_subregion, concept, unit, quality_tier FROM marts.v_panel"
                  " WHERE iso3 = 'NGA' AND concept = 'under5_mortality' AND year = 2010")[0]
    assert (row[0].strip(), row[1], row[2], row[3]) == ("NGA", "Nigeria", "Western Africa", "under5_mortality")
    assert "per 1000" in row[4]
    # Nigeria has under-5 mortality, DTP3 and (one cross-validated) stunting value
    assert q(conn, "SELECT count(*) FROM marts.v_country_latest WHERE iso3 = 'NGA'")[0][0] == 3


@pytest.mark.integration
def test_rebuild_is_idempotent_and_records_parameters(marts):
    conn, run_dir, first = marts
    second = build_marts(conn, run_dir, min_countries=5)
    assert second == first
    params, rows = q(conn, "SELECT parameters, rows FROM marts.build_log WHERE run_id = 'mart1'")[0]
    assert params["ref_year"] == 2015 and params["min_countries"] == 5 and params["trend_start"] == 2000
    assert rows["country_indicator_year"] == first["country_indicator_year"]


@pytest.mark.integration
def test_marts_require_a_core_layer(pg_conn, tmp_path):
    run_dir = snapshot(tmp_path, "mart_nocore")
    with pytest.raises(RuntimeError, match="no core layer"):
        build_marts(pg_conn, run_dir)
