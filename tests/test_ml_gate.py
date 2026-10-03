"""Tests for the backtesting machinery and the decision gate. The evaluation code must be right
before its conclusions mean anything, so these check for leakage, exact baselines and the
decision rule on small synthetic cases with known answers."""
import numpy as np
import pandas as pd
import pytest

from healthbridge.ml import backtest as bt
from healthbridge.ml import gate

# --- baselines on series with known answers ------------------------------------------------

LINE_YEARS = np.arange(2000, 2012)
LINE_VALUES = 10.0 + 2.0 * (LINE_YEARS - 2000)          # an exact straight line


def test_baselines_on_an_exact_line():
    target = 2015.0
    truth = 10.0 + 2.0 * (target - 2000)
    assert bt.last_value(LINE_YEARS, LINE_VALUES, target) == LINE_VALUES[-1]
    assert bt.linear_trend(5)(LINE_YEARS, LINE_VALUES, target) == pytest.approx(truth)
    assert bt.average_change(LINE_YEARS, LINE_VALUES, target) == pytest.approx(truth)


def test_baselines_degrade_gracefully_on_tiny_histories():
    assert bt.linear_trend(5)(np.array([2000]), np.array([7.0]), 2005.0) == 7.0
    assert bt.average_change(np.array([2000]), np.array([7.0]), 2005.0) == 7.0


# --- the backtest never sees the future ----------------------------------------------------

def test_no_forecast_uses_data_after_its_origin():
    seen = []

    def spy(years, values, target):
        seen.append((int(years[-1]), len(years), float(target)))
        return float(values[-1])

    years = np.arange(2000, 2015)
    rows = bt.rolling_origin(years, years.astype(float), {"spy": spy}, (0, 1e9), min_history=5, max_horizon=10)
    for (last_year_seen, n_seen, target), row in zip(seen, rows, strict=True):
        assert last_year_seen == row["origin_year"] < target == row["target_year"]   # history ends at the origin
        assert n_seen == row["n_history"] >= 5


def test_horizon_limits_and_irregular_years():
    years = np.array([1990, 1995, 2000, 2003, 2006, 2012, 2018, 2020])
    rows = bt.rolling_origin(years, np.arange(8.0) + 1, {"m": bt.last_value}, (0, 100), min_history=3, max_horizon=10)
    assert all(1 <= r["horizon"] <= 10 for r in rows)
    first = rows[0]
    assert (first["origin_year"], first["target_year"], first["horizon"]) == (2000, 2003, 3)
    assert not any(r["origin_year"] == 2000 and r["target_year"] == 2012 for r in rows)  # 12 years > the limit
    assert min(r["origin_year"] for r in rows) == 2000                                     # needs 3 prior observations


def test_forecasts_are_clipped_to_the_plausible_range():
    years = np.arange(2000, 2012)
    values = np.linspace(60, 99, 12)                       # steeply rising towards 100%
    rows = bt.rolling_origin(years, values, {"trend": bt.linear_trend(5)}, (0, 100), min_history=5, max_horizon=10)
    assert max(r["pred_trend"] for r in rows) <= 100.0


# --- panel backtest and errors -------------------------------------------------------------

def _panel(rate=0.97):
    rows = []
    for iso, base in (("AAA", 200.0), ("BBB", 100.0), ("CCC", 50.0)):
        for year in range(2000, 2016):
            rows.append({"concept": "under5_mortality", "iso3": iso, "year": year, "value": base * rate ** (year - 2000)})
    return pd.DataFrame(rows)


def test_backtest_panel_errors_match_the_definition():
    result = bt.backtest_panel(_panel(), {"under5_mortality": (0, 1000)})
    assert {"ape_last_value", "ape_linear_trend_5", "ape_average_change"} <= set(result.columns)
    expected = (result["pred_last_value"] - result["actual"]).abs() / result["actual"]
    assert np.allclose(result["ape_last_value"], expected)
    one_year = result[(result["horizon"] == 1) & (result["iso3"] == "AAA")]
    assert np.allclose(one_year["ape_last_value"], 1 / 0.97 - 1)    # repeating a 3% decline is a 3.09% error
    assert (result["ape_linear_trend_5"] < result["ape_last_value"]).mean() > 0.9   # a trend beats no trend here


def test_zero_actuals_are_dropped_not_divided_by():
    panel = _panel()
    panel.loc[(panel["iso3"] == "AAA") & (panel["year"] == 2010), "value"] = 0.0
    result = bt.backtest_panel(panel, {"under5_mortality": (0, 1000)})
    assert (result["actual"] > 0).all() and np.isfinite(result.filter(like="ape_").to_numpy()).all()


def test_bucketing_of_horizons():
    assert [bt.bucket_of(h) for h in (1, 2, 3, 4, 5, 6, 10, 11)] == ["1", "2-3", "2-3", "4-5", "4-5", "6-10", "6-10", None]


# --- uncertainty is resampled by country ---------------------------------------------------

def test_cluster_bootstrap():
    constant = [np.full(10, 0.04) for _ in range(8)]
    assert bt.cluster_bootstrap_median(constant, 100) == (pytest.approx(0.04), pytest.approx(0.04))
    assert all(np.isnan(bt.cluster_bootstrap_median([np.array([0.1])], 50)))     # one country: no interval
    # a median is robust: one outlying country among eight does not move it
    one_bad = [np.full(10, 0.01) for _ in range(7)] + [np.full(10, 0.50)]
    assert bt.cluster_bootstrap_median(one_bad, 300) == (pytest.approx(0.01), pytest.approx(0.01))
    # but when countries split between two very different error levels, resampling countries
    # (not forecasts) correctly yields a wide interval
    split = [np.full(10, 0.01) for _ in range(4)] + [np.full(10, 0.50) for _ in range(4)]
    low, high = bt.cluster_bootstrap_median(split, 400)
    assert low == pytest.approx(0.01) and high == pytest.approx(0.50)
    assert bt.cluster_bootstrap_median(split, 300, seed=1) == bt.cluster_bootstrap_median(split, 300, seed=1)


def test_summary_medians_and_counts():
    result = bt.backtest_panel(_panel(), {"under5_mortality": (0, 1000)})
    summary = bt.summarize(result, resamples=50)
    row = summary[(summary["bucket"] == "1") & (summary["model"] == "last_value")].iloc[0]
    assert row["median_ape"] == pytest.approx(1 / 0.97 - 1) and row["n_countries"] == 3
    assert row["ci_low"] <= row["median_ape"] <= row["ci_high"]
    assert set(summary["bucket"]) == {"1", "2-3", "4-5", "6-10"}


# --- the decision gate ---------------------------------------------------------------------

def _volume(**counts):
    return pd.DataFrame([{"concept": c, "countries": 54, "countries_with_history": n, "median_observations": 20.0,
                          "median_gap_years": 1.0, "share_annual": 1.0} for c, n in counts.items()])


def _summary(concept, ape, instances=500, countries=40):
    return pd.DataFrame([{"concept": concept, "bucket": "4-5", "model": "linear_trend_5", "n_instances": instances,
                          "n_countries": countries, "median_ape": ape, "ci_low": ape, "ci_high": ape}])


def test_criteria_apply_each_threshold_independently():
    volume = _volume(passes=54, thin=10, flat=54, few=54)
    summary = pd.concat([_summary("passes", 0.08), _summary("thin", 0.08), _summary("flat", 0.02),
                         _summary("few", 0.08, instances=100)])
    verdict = gate.evaluate_criteria(volume, summary).set_index("concept")
    assert bool(verdict.loc["passes", "supports_forecasting"])
    assert not verdict.loc["thin", "C1_data"] and not verdict.loc["thin", "supports_forecasting"]
    assert not verdict.loc["flat", "C2_headroom"] and not verdict.loc["flat", "supports_forecasting"]
    assert not verdict.loc["few", "C3_evaluable"] and not verdict.loc["few", "supports_forecasting"]


def test_threshold_is_inclusive_at_five_percent():
    volume = _volume(edge=54)
    assert bool(gate.evaluate_criteria(volume, _summary("edge", 0.05)).iloc[0]["C2_headroom"])
    assert not bool(gate.evaluate_criteria(volume, _summary("edge", 0.0499)).iloc[0]["C2_headroom"])


def test_gate_says_no_go_when_nothing_qualifies():
    volume, summary = _volume(flat=54), _summary("flat", 0.02)
    result = {"volume": volume, "summary": summary, "criteria": gate.evaluate_criteria(volume, summary)}
    text = gate.render_gate_report(result)
    assert "No-go" in text and "no machine-learning model is built" in text


def test_gate_names_the_indicators_that_qualify():
    volume = _volume(good=54, flat=54)
    summary = pd.concat([_summary("good", 0.09), _summary("flat", 0.01)])
    result = {"volume": volume, "summary": summary, "criteria": gate.evaluate_criteria(volume, summary)}
    text = gate.render_gate_report(result)
    assert "**Go**, for these indicators only: good." in text


def test_pre_registered_thresholds_are_pinned():
    # These are the values written down in docs/ml_decision.md before any result was seen.
    assert (gate.MIN_COUNTRIES_WITH_HISTORY, gate.MIN_OBSERVATIONS) == (30, 8)
    assert (gate.MIN_HEADROOM_APE, gate.HEADROOM_BUCKET) == (0.05, "4-5")
    assert (gate.MIN_INSTANCES, gate.MIN_EVAL_COUNTRIES) == (200, 20)


def test_data_volume_summarises_series():
    rows = [{"concept": "x", "iso3": iso, "year": y, "value": 1.0, "quality_tier": "single_evidence_group",
             "outlier_flag": False} for iso, years in (("AAA", range(2000, 2012)), ("BBB", range(2000, 2010, 3)))
            for y in years]
    volume = gate.data_volume(pd.DataFrame(rows)).iloc[0]
    assert (volume["countries"], volume["countries_with_history"]) == (2, 1)    # only AAA has 8+ observations
    assert volume["median_gap_years"] == 1.0 and 0.5 < volume["share_annual"] < 1.0
