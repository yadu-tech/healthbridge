"""Tests for the forecasting instances, models and stage-2 evaluation. The central concern is
leakage: nothing available only after a forecast's origin may influence it, and nothing after a
cut-off may influence training."""
import math

import numpy as np
import pandas as pd
import pytest

from healthbridge.ml import dataset as ds
from healthbridge.ml import models as md
from healthbridge.ml import stage2 as s2
from healthbridge.reference import indicator_ranges

RANGES = indicator_ranges()
BASE = {"under5_mortality": 120.0, "maternal_mortality_ratio": 500.0, "dtp3_coverage": 40.0,
        "measles_mcv1_coverage": 45.0}


def synthetic_panel(n_countries=20, seed=0):
    """Declining mortality and rising coverage with country-specific pace and a little noise."""
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(n_countries):
        iso, region, rate = f"C{k:02d}", ds.REGIONS[k % 5], rng.uniform(0.01, 0.06)
        for concept in ds.CONCEPTS:
            scale, slope = rng.uniform(0.6, 1.4), rng.uniform(0.3, 1.2)
            for year in range(1990, 2025):
                noise = 1 + rng.normal(0, 0.01)
                if "mortality" in concept:
                    value = BASE[concept] * scale * math.exp(-rate * (year - 1990)) * noise
                else:
                    value = min(98.0, (BASE[concept] * scale + slope * (year - 1990)) * noise)
                rows.append({"concept": concept, "iso3": iso, "un_subregion": region, "year": year, "value": value})
    return pd.DataFrame(rows)


# --- features ------------------------------------------------------------------------------

def test_own_features_on_an_exact_exponential_decline():
    years = np.arange(2000, 2013)
    values = 100 * np.exp(-0.05 * (years - 2000))
    lookup = dict(zip(years.tolist(), values.tolist(), strict=True))
    f = ds.own_features(lookup, years, values, 2012)
    assert f["level"] == pytest.approx(math.log(values[-1]))
    assert (f["dlog1"], f["dlog3"], f["dlog5"]) == pytest.approx((-0.05, -0.15, -0.25))
    assert f["avg_dlog"] == pytest.approx(-0.05) and f["vol5"] == pytest.approx(0.0, abs=1e-12)


def test_missing_history_gives_nan_not_a_guess():
    lookup = {2010: 10.0, 2011: 11.0, 2012: 12.0}
    f = ds.own_features(lookup, np.array([2010, 2011, 2012]), np.array([10.0, 11.0, 12.0]), 2012)
    assert not math.isnan(f["dlog1"]) and math.isnan(f["dlog3"]) and math.isnan(f["dlog5"])


def test_features_never_use_anything_after_the_origin():
    panel = synthetic_panel(8)
    original = ds.build_instances(panel, RANGES)
    shifted = panel.assign(value=np.where(panel["year"] > 2010, panel["value"] * 3, panel["value"]))
    mutated = ds.build_instances(shifted, RANGES)
    key = ["concept", "iso3", "origin_year", "horizon"]
    a = original[original["origin_year"] <= 2010].set_index(key).sort_index()
    b = mutated[mutated["origin_year"] <= 2010].set_index(key).sort_index()
    feature_cols = ds.own_features({}, np.array([2000, 2001]), np.array([1.0, 1.0]), 2001).keys()
    cols = [*feature_cols, *[c for c in a.columns if c.startswith("x_")], "last"]
    common = a.index.intersection(b.index)
    pd.testing.assert_frame_equal(a.loc[common, cols], b.loc[common, cols])   # features identical
    far = a.loc[common][a.loc[common, "target_year"] > 2010].index
    assert not np.allclose(a.loc[far, "actual"], b.loc[far, "actual"])        # while the future did change


def test_instances_are_well_formed():
    inst = ds.build_instances(synthetic_panel(6), RANGES)
    assert inst["horizon"].between(1, ds.MAX_HORIZON).all()
    assert (inst["target_year"] - inst["origin_year"] == inst["horizon"]).all()
    assert np.allclose(inst["y"], np.log(inst["actual"] / inst["last"]))
    assert (inst["origin_year"] >= 1990 + ds.MIN_HISTORY - 1).all()           # enough history before every origin
    assert (inst["pred_last_value"] == inst["last"]).all()


def test_cross_indicator_features_describe_the_other_indicators_only():
    inst = ds.build_instances(synthetic_panel(5), RANGES)
    row = inst[inst["concept"] == "dtp3_coverage"].iloc[0]
    assert math.isnan(row["x_dtp3_coverage_level"])                            # not its own series
    assert not math.isnan(row["x_under5_mortality_level"])


def test_linear_trend_baseline_is_exact_on_a_line():
    rows = [{"concept": "dtp3_coverage", "iso3": "AAA", "un_subregion": "Western Africa", "year": y,
             "value": 40.0 + 1.5 * (y - 1990)} for y in range(1990, 2015)]
    inst = ds.build_instances(pd.DataFrame(rows), RANGES, concepts=("dtp3_coverage",))
    assert np.allclose(inst["ape_linear_trend_5"], 0.0, atol=1e-9)
    assert (inst["ape_last_value"] > 0).all()


# --- the split and the models ---------------------------------------------------------------

def test_training_never_contains_a_target_after_the_cutoff():
    inst = ds.build_instances(synthetic_panel(10), RANGES)
    for cutoff in (2005, 2011, 2017):
        train, test = s2.split(inst, cutoff)
        assert (train["target_year"] <= cutoff).all()
        assert (test["origin_year"] == cutoff).all() and (test["target_year"] > cutoff).all()
        assert not set(test["target_year"]) & set(range(1990, cutoff + 1))      # tested years are never trained on


def test_design_matrix_is_fixed_and_keeps_missing_values_missing():
    inst = ds.build_instances(synthetic_panel(5), RANGES).head(30)
    x = md.design_matrix(inst)
    assert list(x.columns) == list(md.design_matrix(inst.iloc[::-1]).columns)
    assert x.filter(like="is_").sum(axis=1).eq(1).all() and x.filter(like="in_").sum(axis=1).eq(1).all()
    row = inst.iloc[0]
    assert x.iloc[0]["h_dlog3"] == pytest.approx(row["horizon"] * row["dlog3"] / 3, nan_ok=True)
    assert x[f"x_{row['concept']}_level"].isna().all()                          # own-series cross feature stays NaN


def test_values_are_clipped_to_the_plausible_range():
    inst = pd.DataFrame({"concept": ["dtp3_coverage", "under5_mortality"], "last": [90.0, 50.0]})
    out = md.to_values(inst, np.array([1.0, -50.0]), RANGES)                    # +172% and a collapse to ~0
    assert out[0] == 100.0 and 0.0 <= out[1] < 1e-6


@pytest.fixture(scope="module")
def stage2_result():
    inst = ds.build_instances(synthetic_panel(24), RANGES)
    results = s2.evaluate_models(inst, RANGES, cutoffs=(2008, 2014), seeds=(0, 1))
    return inst, results


def test_evaluation_scores_only_forecasts_made_at_each_cutoff(stage2_result):
    _, results = stage2_result
    assert set(results["cutoff"]) == {2008, 2014}
    assert (results["origin_year"] == results["cutoff"]).all()
    for column in ("ape_ridge", "ape_gbm", "ape_gbm_s0", "ape_gbm_s1", "ape_last_value"):
        assert np.isfinite(results[column]).all(), column
    assert results["pred_gbm"].between(0, 1000).all()


def test_models_learn_a_strong_signal_and_are_deterministic(stage2_result):
    inst, results = stage2_result
    long_range = results[results["horizon"] >= 4]
    # on smooth synthetic data a pooled model should be competitive with the best simple baseline
    assert long_range["ape_ridge"].median() < 2 * long_range["ape_linear_trend_5"].median() + 0.02
    again = s2.evaluate_models(inst, RANGES, cutoffs=(2008, 2014), seeds=(0, 1))
    pd.testing.assert_series_equal(results["ape_gbm"], again["ape_gbm"])        # same seeds, same answer


# --- the success rule ------------------------------------------------------------------------

def _frame(gap, spread=0.0, n_countries=12, per_country=20, seed=0):
    """Test results where the model's error is ``gap`` below the best baseline, with noise ``spread``."""
    rng = np.random.default_rng(seed)
    rows = []
    for c in range(n_countries):
        for _ in range(per_country):
            base = rng.uniform(0.08, 0.12)
            noise = rng.normal(0, spread)
            row = {"concept": "under5_mortality", "iso3": f"C{c:02d}", "horizon": 4, "ape_last_value": base + 0.05,
                   "ape_linear_trend_5": base, "ape_average_change": base + 0.03, "ape_ridge": base - gap + noise,
                   "ape_gbm": base - gap + noise}
            row.update({f"ape_gbm_s{s}": base - gap + noise for s in s2.SEEDS})
            rows.append(row)
    return pd.DataFrame(rows)


def test_a_clearly_better_model_is_useful():
    verdict = s2.verdicts(s2.summarize_models(_frame(gap=0.03), resamples=200)).set_index("model")
    assert bool(verdict.loc["gbm", "useful"]) and verdict.loc["gbm", "diff_high"] < 0
    assert verdict.loc["gbm", "best_baseline"] == "linear_trend_5"


def test_an_equal_model_is_not_useful():
    verdict = s2.verdicts(s2.summarize_models(_frame(gap=0.0), resamples=200)).set_index("model")
    assert not bool(verdict.loc["gbm", "useful"])


def test_a_lower_median_with_an_interval_that_crosses_zero_is_not_useful():
    noisy = _frame(gap=0.002, spread=0.06, n_countries=8, per_country=6, seed=3)
    verdict = s2.verdicts(s2.summarize_models(noisy, resamples=300)).set_index("model")
    row = verdict.loc["gbm"]
    assert row["diff_low"] < 0 < row["diff_high"] and not bool(row["useful"])   # unproven, so not reported as useful


def test_pre_registered_protocol_values_are_pinned():
    assert s2.CUTOFFS == (2005, 2008, 2011, 2014, 2017) and s2.DECISIVE_BUCKET == "4-5"
    assert md.SEEDS == (0, 1, 2, 3, 4) and set(ds.CONCEPTS) == {
        "under5_mortality", "maternal_mortality_ratio", "dtp3_coverage", "measles_mcv1_coverage"}


# --- diagnostics added after the first real run ---------------------------------------------

def test_by_cutoff_reports_each_cutoff_against_the_same_best_baseline(stage2_result):
    _, results = stage2_result
    summary = s2.summarize_models(results, resamples=50, seeds=(0, 1))
    table = s2.by_cutoff(results, summary)
    assert set(table["cutoff"]) == {2008, 2014} and set(table["concept"]) == set(ds.CONCEPTS)
    chosen = summary[(summary["bucket"] == "4-5") & (summary["model"] == "gbm")].set_index("concept")["best_baseline"]
    assert all(row.best_baseline == chosen[row.concept] for row in table.itertuples())
    assert table["gbm_beats_baseline"].dtype == bool
    one = table.iloc[0]
    subset = results[(results["concept"] == one["concept"]) & (results["cutoff"] == one["cutoff"])
                     & results["horizon"].between(4, 5)]
    assert one["gbm_ape"] == pytest.approx(subset["ape_gbm"].median())


def test_ridge_fit_check_compares_in_and_out_of_sample(stage2_result):
    inst, _ = stage2_result
    check = s2.ridge_fit_check(inst, RANGES, cutoff=2011)
    assert set(check["sample"]) == {"in sample (training)", "out of sample (test)"}
    assert set(check["concept"]) == set(ds.CONCEPTS)
    assert np.isfinite(check[["last_value", "linear_trend_5", "ridge"]].to_numpy()).all()
    assert (check["n"] > 0).all()


def test_report_includes_the_robustness_and_fit_sections(stage2_result):
    inst, results = stage2_result
    summary = s2.summarize_models(results, resamples=50, seeds=(0, 1))
    text = s2.render_models_report(results, summary, s2.verdicts(summary), {2008: 1, 2014: 2},
                                   s2.by_cutoff(results, summary), s2.ridge_fit_check(inst, RANGES, cutoff=2011))
    assert "Is the result driven by one cut-off?" in text and "Why is the ridge model poor?" in text
    assert "median of per-instance differences" in text
