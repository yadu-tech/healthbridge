"""Forecasting instances for the pooled models.

One instance is a forecast of one country's value of one indicator, made at an *origin* year for a
*target* year ``horizon`` years later. Every feature is computed from observations at or before the
origin year, so an instance cannot carry information from its own future. The label is the log
ratio of the target value to the latest value, which makes it scale-free across indicators.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import psycopg

from healthbridge.ml.backtest import BASELINES

CONCEPTS = ("under5_mortality", "maternal_mortality_ratio", "dtp3_coverage", "measles_mcv1_coverage")
REGIONS = ("Eastern Africa", "Middle Africa", "Northern Africa", "Southern Africa", "Western Africa")
MIN_HISTORY = 6      # observations needed before an origin (the longest lookback is 5 years)
MAX_HORIZON = 10


def load_panel(conn: psycopg.Connection, concepts: tuple[str, ...] = CONCEPTS) -> pd.DataFrame:
    """Reconciled values for the chosen indicators, without values in conflict between sources."""
    cur = conn.execute(
        "SELECT concept, trim(iso3) AS iso3, un_subregion, year, value FROM marts.v_panel"
        " WHERE concept = ANY(%s) AND quality_tier <> 'conflict' ORDER BY concept, iso3, year",
        (list(concepts),))
    return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])


def _log_ratio(a: float | None, b: float | None) -> float:
    if a is None or b is None or a <= 0 or b <= 0 or math.isnan(a) or math.isnan(b):
        return float("nan")
    return math.log(a / b)


def own_features(lookup: dict[int, float], hist_years: np.ndarray, hist_values: np.ndarray, t0: int) -> dict:
    """Features of one series at its origin year, from data up to and including the origin."""
    last = float(hist_values[-1])
    changes = [_log_ratio(lookup.get(y), lookup.get(y - 1)) for y in range(t0 - 4, t0 + 1)]
    changes = [c for c in changes if not math.isnan(c)]
    first_year, first_value = int(hist_years[0]), float(hist_values[0])
    return {
        "level": math.log(last),
        "dlog1": _log_ratio(last, lookup.get(t0 - 1)),
        "dlog3": _log_ratio(last, lookup.get(t0 - 3)),
        "dlog5": _log_ratio(last, lookup.get(t0 - 5)),
        "avg_dlog": _log_ratio(last, first_value) / (t0 - first_year) if t0 > first_year else float("nan"),
        "vol5": float(np.std(changes)) if len(changes) >= 3 else float("nan"),
    }


def build_instances(panel: pd.DataFrame, ranges: dict[str, tuple[float, float]],
                    concepts: tuple[str, ...] = CONCEPTS, min_history: int = MIN_HISTORY,
                    max_horizon: int = MAX_HORIZON) -> pd.DataFrame:
    """Every forecastable (country, indicator, origin, horizon), with features, label and baselines."""
    panel = panel[panel["concept"].isin(concepts)]
    region_of = panel.drop_duplicates("iso3").set_index("iso3")["un_subregion"].to_dict()
    series = {key: g.sort_values("year").set_index("year")["value"]
              for key, g in panel.groupby(["iso3", "concept"])}
    rows = []
    for iso3 in sorted(region_of):
        lookups = {c: series[(iso3, c)].to_dict() for c in concepts if (iso3, c) in series}
        for concept in concepts:
            if concept not in lookups:
                continue
            s = series[(iso3, concept)]
            years, values = s.index.to_numpy(), s.to_numpy(float)
            lo, hi = ranges[concept]
            for i in range(min_history - 1, len(years) - 1):
                t0, last = int(years[i]), float(values[i])
                if last <= 0:
                    continue
                hist_y, hist_v = years[:i + 1], values[:i + 1]
                base = {"concept": concept, "iso3": iso3, "un_subregion": region_of[iso3],
                        "origin_year": t0, "last": last}
                base.update(own_features(lookups[concept], hist_y, hist_v, t0))
                for other in concepts:   # what the other indicators say about the same country at the origin
                    lk = lookups.get(other) if other != concept else None
                    now = lk.get(t0) if lk else None
                    base[f"x_{other}_level"] = math.log(now) if now and now > 0 else float("nan")
                    base[f"x_{other}_dlog3"] = _log_ratio(now, lk.get(t0 - 3)) if lk else float("nan")
                trend = {name: fn for name, fn in BASELINES.items()}
                for j in range(i + 1, len(years)):
                    horizon = int(years[j] - t0)
                    if horizon > max_horizon:
                        break
                    actual = float(values[j])
                    if actual <= 0:
                        continue
                    row = dict(base, target_year=int(years[j]), horizon=horizon, actual=actual,
                               y=math.log(actual / last))
                    for name, fn in trend.items():
                        row[f"pred_{name}"] = float(np.clip(fn(hist_y, hist_v, float(years[j])), lo, hi))
                        row[f"ape_{name}"] = abs(row[f"pred_{name}"] - actual) / actual
                    rows.append(row)
    return pd.DataFrame(rows)
