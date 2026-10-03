"""Stage 2: pooled models against the baselines, evaluated exactly as pre-registered.

Protocol (docs/ml_decision.md): for each cut-off year T a model is trained only on instances whose
*target* year is at most T, and then forecasts every instance whose *origin* year is T. Training
never sees a year after T. A model is useful for an indicator only if its median absolute
percentage error at 4-5 years is below the best baseline's AND the 95% interval of the paired
difference (resampling countries) lies entirely below zero. Gradient boosting is run with five
seeds; its headline forecast is the mean of the seeds, and each seed's own error is reported as a
stability check.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from healthbridge.ml.backtest import HORIZON_BUCKETS, bucket_of, cluster_bootstrap_median
from healthbridge.ml.dataset import CONCEPTS
from healthbridge.ml.models import SEEDS, design_matrix, make_gbm, make_ridge, to_values

CUTOFFS = (2005, 2008, 2011, 2014, 2017)
BASELINE_NAMES = ("last_value", "linear_trend_5", "average_change")
MODEL_NAMES = ("ridge", "gbm")
DECISIVE_BUCKET = "4-5"
MIN_TRAIN = 500


def split(instances: pd.DataFrame, cutoff: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Train on targets up to the cut-off; test on forecasts made at the cut-off year."""
    train = instances[instances["target_year"] <= cutoff]
    test = instances[instances["origin_year"] == cutoff]
    return train, test


def evaluate_models(instances: pd.DataFrame, ranges: dict[str, tuple[float, float]],
                    cutoffs: tuple[int, ...] = CUTOFFS, seeds: tuple[int, ...] = SEEDS) -> pd.DataFrame:
    """Test instances for every cut-off, with each model's absolute percentage error."""
    frames = []
    for cutoff in cutoffs:
        train, test = split(instances, cutoff)
        if len(train) < MIN_TRAIN or test.empty:
            continue
        x_train, x_test = design_matrix(train), design_matrix(test)
        out = test.copy()
        out["cutoff"] = cutoff
        out["n_train"] = len(train)

        ridge = make_ridge().fit(x_train, train["y"])
        out["pred_ridge"] = to_values(test, ridge.predict(x_test), ranges)

        seed_logs = []
        for seed in seeds:
            fitted = make_gbm(seed).fit(x_train, train["y"])
            log_pred = fitted.predict(x_test)
            seed_logs.append(log_pred)
            out[f"pred_gbm_s{seed}"] = to_values(test, log_pred, ranges)
        out["pred_gbm"] = to_values(test, np.mean(seed_logs, axis=0), ranges)

        for name in ("ridge", "gbm", *[f"gbm_s{s}" for s in seeds]):
            out[f"ape_{name}"] = (out[f"pred_{name}"] - out["actual"]).abs() / out["actual"]
        frames.append(out)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def summarize_models(results: pd.DataFrame, resamples: int = 500, seeds: tuple[int, ...] = SEEDS) -> pd.DataFrame:
    """Per indicator and horizon bucket: each model against the best baseline, paired by instance."""
    data = results.assign(bucket=results["horizon"].map(bucket_of)).dropna(subset=["bucket"])
    rows = []
    for (concept, bucket), group in data.groupby(["concept", "bucket"], sort=True):
        medians = {b: group[f"ape_{b}"].median() for b in BASELINE_NAMES}
        best = min(medians, key=medians.get)            # chosen on the test data: favours the baseline
        by_country = {iso: g for iso, g in group.groupby("iso3")}
        for model in (*BASELINE_NAMES, *MODEL_NAMES):
            diff = group[f"ape_{model}"] - group[f"ape_{best}"]
            per_country = [(g[f"ape_{model}"] - g[f"ape_{best}"]).to_numpy() for g in by_country.values()]
            low, high = cluster_bootstrap_median(per_country, resamples)
            row = {"concept": concept, "bucket": bucket, "model": model, "n_instances": len(group),
                   "n_countries": len(by_country), "median_ape": float(group[f"ape_{model}"].median()),
                   "best_baseline": best, "best_baseline_ape": float(medians[best]),
                   "median_diff": float(diff.median()), "diff_low": low, "diff_high": high,
                   "share_better": float((diff < 0).mean())}
            if model == "gbm":
                spread = [group[f"ape_gbm_s{s}"].median() for s in seeds]
                row.update(seed_min=float(min(spread)), seed_max=float(max(spread)))
            rows.append(row)
    return pd.DataFrame(rows)


def verdicts(summary: pd.DataFrame) -> pd.DataFrame:
    """Apply the pre-registered success rule at the 4-5 year horizon."""
    rows = []
    for concept in sorted(summary["concept"].unique()):
        part = summary[(summary["concept"] == concept) & (summary["bucket"] == DECISIVE_BUCKET)]
        for model in MODEL_NAMES:
            r = part[part["model"] == model]
            if r.empty:
                continue
            r = r.iloc[0]
            beats = bool(r["median_ape"] < r["best_baseline_ape"])
            significant = bool(r["diff_high"] < 0)
            rows.append({"concept": concept, "model": model, "best_baseline": r["best_baseline"],
                         "median_ape": float(r["median_ape"]), "baseline_ape": float(r["best_baseline_ape"]),
                         "median_diff": float(r["median_diff"]), "diff_low": float(r["diff_low"]),
                         "diff_high": float(r["diff_high"]), "lower_error": beats, "interval_below_zero": significant,
                         "useful": beats and significant})
    return pd.DataFrame(rows)


def by_cutoff(results: pd.DataFrame, summary: pd.DataFrame) -> pd.DataFrame:
    """At the decisive horizon: does each model beat the best baseline at every cut-off, or only on average?"""
    best = (summary[(summary["bucket"] == DECISIVE_BUCKET) & (summary["model"] == "gbm")]
            .set_index("concept")["best_baseline"].to_dict())
    data = results[results["horizon"].map(bucket_of) == DECISIVE_BUCKET]
    rows = []
    for (concept, cutoff), g in data.groupby(["concept", "cutoff"]):
        base = f"ape_{best[concept]}"
        rows.append({"concept": concept, "cutoff": int(cutoff), "n_instances": len(g),
                     "best_baseline": best[concept], "baseline_ape": float(g[base].median()),
                     "ridge_ape": float(g["ape_ridge"].median()), "gbm_ape": float(g["ape_gbm"].median()),
                     "gbm_beats_baseline": bool(g["ape_gbm"].median() < g[base].median())})
    return pd.DataFrame(rows)


def ridge_fit_check(instances: pd.DataFrame, ranges: dict[str, tuple[float, float]], cutoff: int = 2014) -> pd.DataFrame:
    """Is a poor ridge result a leak, an overfit or a poor fit? Compare its error in and out of sample at 1 year.

    A leak or an overfit would make it look good in sample and bad out of sample; a poor fit is bad in both.
    """
    train, test = split(instances, cutoff)
    x_train, x_test = design_matrix(train), design_matrix(test)
    ridge = make_ridge().fit(x_train, train["y"])
    rows = []
    for label, frame, x in (("in sample (training)", train, x_train), ("out of sample (test)", test, x_test)):
        frame = frame[frame["horizon"] == 1]
        pred = to_values(frame, ridge.predict(x.loc[frame.index]), ranges)
        ape = pd.Series(np.abs(pred - frame["actual"].to_numpy()) / frame["actual"].to_numpy(), index=frame.index)
        for concept, g in frame.groupby("concept"):
            rows.append({"concept": concept, "sample": label, "last_value": float(g["ape_last_value"].median()),
                         "linear_trend_5": float(g["ape_linear_trend_5"].median()),
                         "ridge": float(ape[g.index].median()), "n": len(g)})
    return pd.DataFrame(rows)


def render_models_report(results: pd.DataFrame, summary: pd.DataFrame, verdict: pd.DataFrame,
                         train_sizes: dict[int, int], robustness: pd.DataFrame | None = None,
                         ridge_check: pd.DataFrame | None = None) -> str:
    def pct(x):
        return "n/a" if pd.isna(x) else f"{100 * x:.1f}%"

    def table(headers, body):
        return (["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
                + ["| " + " | ".join(str(c) for c in row) + " |" for row in body])

    lines = [
        "# Stage 2: pooled models against the baselines",
        "",
        ("Generated by `python -m healthbridge.ml models`. The protocol was fixed in "
        "[../ml_decision.md](../ml_decision.md) before any model was built: panel-level rolling origin with "
        f"cut-offs {', '.join(str(c) for c in sorted(train_sizes))}; each model is trained only on instances "
        "whose target year is at most the cut-off, then forecasts from the cut-off year. Errors are absolute "
        "percentage errors. *Best baseline* is the baseline with the lowest median error on the same test "
        "instances, which is chosen on the test data and so is a conservative comparison for the models."),
        "",
        "Test instances: "
        + f"{len(results):,} across {results['iso3'].nunique()} countries and {results['concept'].nunique()} indicators; "
        + "training instances per cut-off: " + ", ".join(f"{c}: {n:,}" for c, n in sorted(train_sizes.items())) + ".",
        "",
        "## 1. Verdict (4-5 year horizon)",
        "",
        ("A model is **useful** only if its median error is below the best baseline's **and** the 95% interval of "
        "the paired difference (resampling countries) lies entirely below zero."),
        "",
    ]
    lines += table(["Indicator", "Model", "Median error", "Best baseline", "Its median error",
                    "Paired difference (95% interval)", "Useful"],
                   [[r.concept, r.model, pct(r.median_ape), r.best_baseline, pct(r.baseline_ape),
                     f"{100 * r.median_diff:+.2f} pp ({100 * r.diff_low:+.2f} to {100 * r.diff_high:+.2f})",
                     "**yes**" if r.useful else "no"] for r in verdict.itertuples()])
    lines += ["", "## 2. All horizons", "",
              ("Median absolute percentage error; the paired difference is against the best baseline for that "
              "indicator and horizon (negative is better, in percentage points). Gradient boosting shows the "
              "range of its five seeds."), ""]
    for concept in sorted(summary["concept"].unique()):
        lines += [f"**{concept}**", ""]
        body = []
        for lo, hi, bucket in HORIZON_BUCKETS:
            part = summary[(summary["concept"] == concept) & (summary["bucket"] == bucket)].set_index("model")
            if part.empty:
                continue
            gbm = part.loc["gbm"]
            body.append([bucket, f"{int(part.iloc[0].n_instances):,}", part.iloc[0].best_baseline,
                         pct(part.iloc[0].best_baseline_ape), pct(part.loc["ridge"].median_ape),
                         f"{pct(gbm.median_ape)} ({pct(gbm.seed_min)}-{pct(gbm.seed_max)})",
                         (f"{100 * part.loc['ridge'].median_diff:+.2f} ({100 * part.loc['ridge'].diff_low:+.2f} "
                         f"to {100 * part.loc['ridge'].diff_high:+.2f})"),
                         f"{100 * gbm.median_diff:+.2f} ({100 * gbm.diff_low:+.2f} to {100 * gbm.diff_high:+.2f})"])
        lines += table(["Years ahead", "Instances", "Best baseline", "Its error", "Ridge", "GBM (seed range)",
                        "Ridge vs best", "GBM vs best"], body) + [""]
    if robustness is not None and len(robustness):
        lines += ["## 3. Is the result driven by one cut-off?", "",
                  ("Median error at 4-5 years at each cut-off: the best baseline, ridge and gradient boosting. "
                   "A consistent win across cut-offs is stronger evidence than a win on average."), ""]
        lines += table(["Indicator", "Cut-off", "Instances", "Best baseline", "Its error", "Ridge", "GBM",
                        "GBM beats baseline"],
                       [[r.concept, r.cutoff, r.n_instances, r.best_baseline, pct(r.baseline_ape), pct(r.ridge_ape),
                         pct(r.gbm_ape), "yes" if r.gbm_beats_baseline else "no"] for r in robustness.itertuples()])
        wins = robustness.groupby("concept")["gbm_beats_baseline"].agg(["sum", "count"])
        lines += ["", "Cut-offs at which gradient boosting beats the best baseline: "
                  + "; ".join(f"{c} {int(w['sum'])} of {int(w['count'])}" for c, w in wins.iterrows()) + ".", ""]
    if ridge_check is not None and len(ridge_check):
        lines += ["## 4. Why is the ridge model poor? A fit check", "",
                  ("The ridge model is much worse than a trivial baseline at one year. To tell a leak or an overfit "
                   "from a poor fit, its median error at one year is shown on the data it was trained on and on "
                   "unseen data (cut-off 2014). A leak or overfit would look good in sample and bad out of sample; "
                   "a poor fit is bad in both. A single pooled linear model cannot capture how four different "
                   "indicators move. It is reported as pre-registered and was not changed after seeing results."), ""]
        lines += table(["Indicator", "Sample", "Last value", "Linear trend", "Ridge", "Instances"],
                       [[r.concept, r.sample, pct(r.last_value), pct(r.linear_trend_5), pct(r.ridge), r.n]
                        for r in ridge_check.itertuples()])
        lines += [""]
    lines += [
        "## 5. How to read this",
        "",
        ("- **The paired difference is the median of per-instance differences.** It can differ in sign from the "
         "difference between two medians. A model can have a lower median error yet be typically worse instance "
         "by instance, which is why the rule requires the paired interval and not only the medians."),
        ("- **Pseudo out-of-sample.** The series are final-vintage modelled estimates, so the history a model sees "
        "already carries information from later years. Real-time forecast error would be larger."),
        "- **Forecasting a model's output.** Skill is skill at extrapolating another model's estimates.",
        "- **Not a ranking of countries, and not for policy.** Results are by indicator and horizon.",
        ("- **Shocks.** Cut-offs and targets that straddle 2020-2021 include the pandemic fall in immunization "
        "coverage, which no method anticipates."),
        ("- **Few effective cut-offs.** Five cut-offs share overlapping data, and countries are not independent; "
        "the country-level interval accounts for the second but not fully for the first."),
        "",
    ]
    return "\n".join(lines)


def run_stage2(instances: pd.DataFrame, ranges: dict[str, tuple[float, float]], resamples: int = 500,
               cutoffs: tuple[int, ...] = CUTOFFS) -> dict:
    results = evaluate_models(instances, ranges, cutoffs=cutoffs)
    summary = summarize_models(results, resamples=resamples)
    verdict = verdicts(summary)
    sizes = results.groupby("cutoff")["n_train"].first().to_dict()
    robustness = by_cutoff(results, summary)
    ridge_check = ridge_fit_check(instances, ranges)
    return {"results": results, "summary": summary, "verdict": verdict, "robustness": robustness,
            "ridge_check": ridge_check,
            "report": render_models_report(results, summary, verdict, sizes, robustness, ridge_check),
            "indicators": sorted(set(CONCEPTS) & set(results["concept"]))}
