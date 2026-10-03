"""Rolling-origin backtesting for country-level indicator forecasts.

For every country series and every origin observation, each model sees only the observations up to
and including that origin and forecasts every later observation within ``max_horizon`` years. This
is the forecasting analogue of a train/test split that cannot leak the future: random splits would
let a model train on years after the ones it is scored on.

The unit of independence is the country, not the forecast: forecasts from one country's series
are strongly dependent, so uncertainty is estimated by resampling countries.
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

Forecaster = Callable[[np.ndarray, np.ndarray, float], float]

HORIZON_BUCKETS = ((1, 1, "1"), (2, 3, "2-3"), (4, 5, "4-5"), (6, 10, "6-10"))
MIN_HISTORY = 5       # observations needed before a forecast is made
MAX_HORIZON = 10      # years between the origin and the target
BOOTSTRAP_RESAMPLES = 500


# --- baselines -----------------------------------------------------------------------------

def last_value(years: np.ndarray, values: np.ndarray, target: float) -> float:
    return float(values[-1])


def linear_trend(k: int = 5) -> Forecaster:
    """Least-squares line through the last ``k`` observations, extrapolated to the target year."""
    def forecast(years: np.ndarray, values: np.ndarray, target: float) -> float:
        y, v = years[-k:].astype(float), values[-k:].astype(float)
        if len(y) < 2 or y[-1] == y[0]:
            return float(v[-1])
        slope, intercept = np.polyfit(y, v, 1)
        return float(intercept + slope * target)
    return forecast


def average_change(years: np.ndarray, values: np.ndarray, target: float) -> float:
    """Continue the average annual change observed over the whole history."""
    if len(years) < 2 or years[-1] == years[0]:
        return float(values[-1])
    slope = (values[-1] - values[0]) / (years[-1] - years[0])
    return float(values[-1] + slope * (target - years[-1]))


BASELINES: dict[str, Forecaster] = {
    "last_value": last_value,
    "linear_trend_5": linear_trend(5),
    "average_change": average_change,
}


# --- backtest ------------------------------------------------------------------------------

def rolling_origin(years: np.ndarray, values: np.ndarray, models: dict[str, Forecaster],
                   bounds: tuple[float, float], min_history: int = MIN_HISTORY,
                   max_horizon: int = MAX_HORIZON) -> list[dict]:
    """All (origin, target) forecasts for one series. Forecasts are clipped to ``bounds``."""
    lo, hi = bounds
    rows = []
    for i in range(min_history - 1, len(years) - 1):
        history_years, history_values = years[:i + 1], values[:i + 1]
        for j in range(i + 1, len(years)):
            horizon = int(years[j] - years[i])
            if horizon > max_horizon:
                break
            row = {"origin_year": int(years[i]), "target_year": int(years[j]), "horizon": horizon,
                   "n_history": i + 1, "actual": float(values[j])}
            for name, model in models.items():
                row[f"pred_{name}"] = float(np.clip(model(history_years, history_values, float(years[j])), lo, hi))
            rows.append(row)
    return rows


def backtest_panel(panel: pd.DataFrame, ranges: dict[str, tuple[float, float]],
                   models: dict[str, Forecaster] | None = None, min_history: int = MIN_HISTORY,
                   max_horizon: int = MAX_HORIZON) -> pd.DataFrame:
    """Backtest every (indicator, country) series in a long panel (concept, iso3, year, value)."""
    models = models or BASELINES
    frames = []
    for (concept, iso3), group in panel.groupby(["concept", "iso3"], sort=True):
        group = group.sort_values("year")
        rows = rolling_origin(group["year"].to_numpy(), group["value"].to_numpy(), models,
                              ranges[concept], min_history, max_horizon)
        if rows:
            frame = pd.DataFrame(rows)
            frame.insert(0, "iso3", iso3)
            frame.insert(0, "concept", concept)
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, ignore_index=True)
    result = result[result["actual"] > 0]            # a percentage error is undefined at zero
    for name in models:
        result[f"ape_{name}"] = (result[f"pred_{name}"] - result["actual"]).abs() / result["actual"]
    return result.reset_index(drop=True)


def bucket_of(horizon: int) -> str | None:
    for lo, hi, label in HORIZON_BUCKETS:
        if lo <= horizon <= hi:
            return label
    return None


# --- summary with a country-level bootstrap ------------------------------------------------

def cluster_bootstrap_median(groups: list[np.ndarray], resamples: int = BOOTSTRAP_RESAMPLES,
                             seed: int = 0) -> tuple[float, float]:
    """95% interval for the median of pooled errors, resampling whole countries."""
    rng = np.random.default_rng(seed)
    n = len(groups)
    if n < 2:
        return (float("nan"), float("nan"))
    medians = np.empty(resamples)
    for b in range(resamples):
        pick = rng.integers(0, n, n)
        medians[b] = np.median(np.concatenate([groups[k] for k in pick]))
    return (float(np.percentile(medians, 2.5)), float(np.percentile(medians, 97.5)))


def summarize(backtest: pd.DataFrame, models: list[str] | None = None,
              resamples: int = BOOTSTRAP_RESAMPLES) -> pd.DataFrame:
    """Median absolute percentage error by indicator, horizon bucket and model."""
    models = models or [c[4:] for c in backtest.columns if c.startswith("ape_")]
    data = backtest.assign(bucket=backtest["horizon"].map(bucket_of)).dropna(subset=["bucket"])
    rows = []
    for (concept, bucket), group in data.groupby(["concept", "bucket"], sort=True):
        by_country = {iso: g for iso, g in group.groupby("iso3")}
        for model in models:
            errors = [g[f"ape_{model}"].to_numpy() for g in by_country.values()]
            low, high = cluster_bootstrap_median(errors, resamples)
            rows.append({"concept": concept, "bucket": bucket, "model": model, "n_instances": len(group),
                         "n_countries": len(by_country), "median_ape": float(group[f"ape_{model}"].median()),
                         "ci_low": low, "ci_high": high})
    return pd.DataFrame(rows)
