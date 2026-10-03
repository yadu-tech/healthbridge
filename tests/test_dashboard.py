"""Dashboard tests: the data layer against a database, chart specifications, the palette, and a
headless run of every page. The marts dataset is the hand-built one from test_marts."""
import math
from pathlib import Path

import pandas as pd
import pytest

from healthbridge.core.build import build_core
from healthbridge.dashboard import charts, data
from healthbridge.dashboard import theme as th
from healthbridge.dq.baseline import compute_baseline
from healthbridge.marts.build import build_marts
from healthbridge.staging.load import load_snapshot
from tests.test_marts import DTP3_LINE, U5_BASE, dtp3, snapshot, u5

APP = Path(__file__).resolve().parents[1] / "src" / "healthbridge" / "dashboard" / "app.py"


@pytest.fixture
def dash(pg_conn, tmp_path):
    """The hand-built marts dataset from test_marts, built into the throwaway database."""
    run_dir = snapshot(tmp_path)
    load_snapshot(pg_conn, run_dir)
    build_core(pg_conn, run_dir, dependence_min_shared=1)
    summary = build_marts(pg_conn, run_dir, min_countries=5)
    return pg_conn, run_dir, summary


# --- palette and theme (no database) -------------------------------------------------------

def test_palette_is_the_validated_reference_palette():
    # Guards against an accidental edit: these hex values were checked with the palette validator.
    assert th.LIGHT.series == ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")
    assert th.DARK.series == ("#3987e5", "#d95926", "#199e70", "#c98500")
    assert (th.LIGHT.surface, th.DARK.surface) == ("#fcfcfb", "#1a1a19")
    assert th.get_theme("dark") is th.DARK and th.get_theme(None) is th.LIGHT


def test_tiers_use_distinct_slots_in_the_first_three():
    assert set(th.TIER_SLOT.values()) == {0, 1, 2}
    assert len(set(th.tier_colors(th.LIGHT))) == 3


def test_colour_follows_the_entity_not_its_position():
    slots = th.assign_slots({}, ["Nigeria", "Ethiopia"])
    assert slots == {"Nigeria": 0, "Ethiopia": 1}
    # removing the first country must not repaint the survivor
    assert th.assign_slots(slots, ["Ethiopia"]) == {"Ethiopia": 1}
    # a newcomer takes the lowest free slot
    assert th.assign_slots({"Ethiopia": 1}, ["Ethiopia", "Kenya"]) == {"Ethiopia": 1, "Kenya": 0}
    full = th.assign_slots({}, ["a", "b", "c", "d"])
    assert sorted(full.values()) == [0, 1, 2, 3]
    with pytest.raises(ValueError):
        th.assign_slots(full, ["a", "b", "c", "d", "e"])   # no fifth colour is ever invented


def test_strip_plot_jitter_is_deterministic_and_bounded():
    values = {iso: charts._jitter(iso) for iso in ("NGA", "KEN", "GHA", "ETH", "ZAF")}
    assert values == {iso: charts._jitter(iso) for iso in values}
    assert all(-0.3 <= v <= 0.3 for v in values.values())


# --- charts build valid specifications -----------------------------------------------------

def _panel_frame():
    years = list(range(2000, 2011))
    df = pd.DataFrame({"iso3": "NGA", "country": "Nigeria", "un_subregion": "Western Africa", "year": years,
                       "value": [100 - 3 * i for i in range(11)], "quality_tier": "single_evidence_group",
                       "outlier_flag": [False] * 5 + [True] + [False] * 5,
                       "source_disagreement": False, "n_sources": 3})
    df.loc[2, "quality_tier"] = "conflict"
    return df


@pytest.mark.parametrize("theme", [th.LIGHT, th.DARK])
def test_every_chart_produces_a_valid_vega_lite_spec(theme):
    trust = pd.DataFrame({"concept": ["under5_mortality", "stunting_prevalence"],
                          "share_single_evidence": [1.0, 0.77], "share_cross_validated": [0.0, 0.19],
                          "share_conflict": [0.0, 0.04]})
    panel = _panel_frame()
    band = pd.DataFrame({"year": range(2000, 2011), "median_value": 90.0, "min_value": 60.0, "max_value": 120.0})
    scatter = pd.DataFrame({"iso3": ["NGA", "KEN"], "country": ["Nigeria", "Kenya"],
                            "un_subregion": ["Western Africa", "Eastern Africa"], "x": [40.0, 80.0], "y": [120.0, 50.0]})
    equity = pd.DataFrame({"iso3": ["NGA", "KEN"], "country": ["Nigeria", "Kenya"],
                           "un_subregion": ["Western Africa", "Eastern Africa"], "year": [2010, 2014],
                           "value_a": [240.0, 44.0], "value_b": [120.0, 22.0], "ratio": [2.0, 2.0], "source": ["who", "unicef"]})
    fan = pd.DataFrame({"concept": ["under5_mortality", "dtp3_coverage"], "before": [57.9, 1.0], "after": [1.0, 1.0]})
    built = [
        charts.trust_bars(trust, theme),
        charts.country_multiple(panel, band, "Under-5 mortality", "per 1,000 live births", theme),
        charts.compare_lines(panel, {"Nigeria": theme.series[0]}, "per 1,000 live births", theme),
        charts.scatter_chart(scatter, None, None, "x", "y", theme),
        charts.scatter_chart(scatter, "Western Africa", "Kenya", "x", "y", theme),
        charts.equity_strip(equity, "poorest", "richest", theme),
        charts.fanout_bars(fan, theme),
    ]
    for chart in built:
        spec = charts.style(chart, theme, height=200).to_dict()   # validates against the Vega-Lite schema
        assert spec["config"]["background"] == theme.surface
        assert spec["config"]["axis"]["gridDash"] == []             # solid hairline grid, never dashed


def test_scatter_uses_one_colour_unless_a_group_is_highlighted():
    df = pd.DataFrame({"iso3": ["NGA", "KEN"], "country": ["Nigeria", "Kenya"],
                       "un_subregion": ["Western Africa", "Eastern Africa"], "x": [1.0, 2.0], "y": [3.0, 4.0]})
    plain = charts.scatter_chart(df, None, None, "x", "y", th.LIGHT).to_dict()
    assert th.LIGHT.context not in str(plain)          # no grey context when nothing is singled out
    focus = charts.scatter_chart(df, "Western Africa", None, "x", "y", th.LIGHT).to_dict()
    assert th.LIGHT.context in str(focus)              # grey only appears alongside a highlighted group


# --- data layer (database) -----------------------------------------------------------------

@pytest.mark.integration
def test_snapshot_and_reference_lookups(dash):
    conn, _, summary = dash
    info = data.snapshot_info(conn)
    assert info["run_id"] == "mart1" and info["n_countries"] == 54
    assert (info["year_min"], info["year_max"]) == (2000, 2016)
    assert info["rows"]["country_indicator_year"] == summary["country_indicator_year"]
    assert len(data.countries(conn)) == 54 and len(data.indicators(conn)) == 7
    assert data.countries(conn).iloc[0]["iso3"] == data.countries(conn).iloc[0]["iso3"].strip()


@pytest.mark.integration
def test_panel_and_region_band(dash):
    conn, _, _ = dash
    df = data.panel(conn, "dtp3_coverage", ["NGA", "KEN"])
    assert set(df["iso3"]) == {"NGA", "KEN"} and len(df) == 34
    nga = df[df["iso3"] == "NGA"].set_index("year")["value"]
    assert nga[2010] == pytest.approx(dtp3("NGA", 2010))
    band = data.region_band(conn, "dtp3_coverage", "Western Africa").set_index("year")
    west = sorted(dtp3(c, 2010) for c in ("NGA", "GHA", "SEN", "MLI"))
    assert band.loc[2010, "median_value"] == pytest.approx((west[1] + west[2]) / 2)
    assert (band.loc[2010, "n_countries"], band.loc[2010, "n_possible"]) == (4, 16)


@pytest.mark.integration
def test_trust_and_country_summaries(dash):
    conn, _, _ = dash
    trust = data.trust(conn).set_index("concept")
    assert trust.loc["stunting_prevalence", "share_conflict"] == pytest.approx(0.5)
    assert trust.loc["under5_mortality", "share_single_evidence"] == pytest.approx(1.0)
    latest = data.country_latest(conn, "NGA").set_index("concept")
    assert latest.loc["under5_mortality", "latest_year"] == 2016
    trends = data.country_trends(conn, "NGA").set_index("concept")
    assert trends.loc["under5_mortality", "annual_pct_change"] == pytest.approx(-0.03, abs=1e-9)


@pytest.mark.integration
def test_scatter_matches_the_association_it_is_drawn_for(dash):
    conn, _, _ = dash
    for basis in ("level", "change"):
        df = data.scatter(conn, "dtp3_coverage", "under5_mortality", basis)
        stat = data.association(conn, "dtp3_coverage", "under5_mortality", basis)
        assert len(df) == stat["n"] == 6                  # the points are exactly the countries behind rho
        assert -1 <= stat["rho"] <= 1 and stat["ci_low"] < stat["rho"] < stat["ci_high"]
    level = data.scatter(conn, "dtp3_coverage", "under5_mortality", "level").set_index("iso3")
    assert level.loc["NGA", "x"] == pytest.approx(dtp3("NGA", 2015))
    assert level.loc["NGA", "y"] == pytest.approx(u5("NGA", 2015))
    change = data.scatter(conn, "dtp3_coverage", "under5_mortality", "change").set_index("iso3")
    assert change.loc["KEN", "x"] == pytest.approx(DTP3_LINE["KEN"][1] * 15)
    assert change.loc["KEN", "y"] == pytest.approx(u5("KEN", 2015) - U5_BASE["KEN"])
    # the order of the two indicators does not matter for the statistic
    flipped = data.association(conn, "under5_mortality", "dtp3_coverage", "level")
    assert flipped["rho"] == pytest.approx(data.association(conn, "dtp3_coverage", "under5_mortality", "level")["rho"])


@pytest.mark.integration
def test_equity_returns_one_latest_gap_per_country(dash):
    conn, _, _ = dash
    wealth = data.equity(conn, "under5_mortality", "wealth")
    assert list(wealth["iso3"]) == ["NGA"] and wealth.iloc[0]["ratio"] == pytest.approx(2.0)
    res = data.equity(conn, "stunting_prevalence", "residence").iloc[0]
    assert (res["iso3"], res["year"], res["ratio"]) == ("KEN", 2014, pytest.approx(2.0))
    options = data.equity_options(conn)
    assert {("under5_mortality", "wealth"), ("under5_mortality", "sex"), ("stunting_prevalence", "residence")} <= {
        (r.concept, r.dimension) for r in options.itertuples()}


@pytest.mark.integration
def test_pipeline_summary_before_and_after(dash):
    conn, run_dir, _ = dash
    empty = data.pipeline_summary(conn)
    assert empty["scores"] is None and empty["accounting"]["staged"] > 0   # no dq runs yet: still no error
    compute_baseline(conn, run_dir, stage="staging")
    compute_baseline(conn, run_dir, stage="core")
    summary = data.pipeline_summary(conn)
    scores = summary["scores"].set_index("dimension")
    assert list(summary["scores"]["dimension"]) == ["completeness", "validity", "uniqueness", "consistency", "composite"]
    assert scores.loc["uniqueness", "after"] == pytest.approx(1.0) and scores.loc["uniqueness", "before"] < 1.0
    fan = summary["fanout"].set_index("concept")      # only stunting has all three sources in this dataset
    assert list(fan.index) == ["stunting_prevalence"]
    assert fan.loc["stunting_prevalence", "after"] == pytest.approx(1.0)
    assert fan.loc["stunting_prevalence", "before"] >= 1.0 and not math.isnan(fan.loc["stunting_prevalence", "before"])


# --- the app, headless ---------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.parametrize("page", ["Overview", "Country profile", "Compare countries",
                                                 "Relationships", "Equity gaps", "Pipeline and quality"])
def test_every_page_runs_without_error(dash, monkeypatch, page):
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    conn, run_dir, _ = dash
    compute_baseline(conn, run_dir, stage="staging")
    compute_baseline(conn, run_dir, stage="core")
    conn.commit()                                   # the app connects separately and must see the data
    monkeypatch.setenv("POSTGRES_DB", "healthbridge_test")
    st.cache_data.clear()
    app = AppTest.from_file(str(APP), default_timeout=90)
    app.run()
    assert not app.exception
    app.sidebar.radio(key="page").set_value(page).run()
    assert not app.exception, [e.value for e in app.exception]
    assert not app.error
    if page == "Overview":
        assert any(m.label == "Countries" and m.value == "54" for m in app.metric)
    if page == "Relationships":
        assert any(m.label == "Countries" and m.value == "6" for m in app.metric)
