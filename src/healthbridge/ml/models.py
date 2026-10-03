"""Pooled models for forecasting: a regularised linear model and gradient-boosted trees.

Both predict the log ratio of the target to the latest value from features available at the origin,
then convert back to the indicator's units and clip to its plausible range. Hyperparameters are
fixed here and were not tuned on any test result.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import RidgeCV
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from healthbridge.ml.dataset import CONCEPTS, REGIONS

OWN = ["horizon", "level", "dlog1", "dlog3", "dlog5", "avg_dlog", "vol5"]
CROSS = [f"x_{c}_{kind}" for c in CONCEPTS for kind in ("level", "dlog3")]
INTERACTIONS = ["h_dlog1", "h_dlog3", "h_dlog5", "h_avg"]     # a linear model needs these to extrapolate a trend
SEEDS = (0, 1, 2, 3, 4)


def design_matrix(instances: pd.DataFrame) -> pd.DataFrame:
    """Numeric features, interaction terms and fixed one-hot columns; NaN where a feature is unavailable."""
    x = instances[OWN + CROSS].copy()
    h = instances["horizon"]
    x["h_dlog1"] = h * instances["dlog1"]
    x["h_dlog3"] = h * instances["dlog3"] / 3
    x["h_dlog5"] = h * instances["dlog5"] / 5
    x["h_avg"] = h * instances["avg_dlog"]
    for concept in CONCEPTS:
        x[f"is_{concept}"] = (instances["concept"] == concept).astype(float)
    for region in REGIONS:
        x[f"in_{region}"] = (instances["un_subregion"] == region).astype(float)
    return x.astype(float)


def make_ridge():
    return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                         RidgeCV(alphas=(0.1, 1.0, 10.0, 100.0)))


def make_gbm(seed: int):
    # shallow, regularised and feature-subsampled; the seed controls the feature subsampling
    return HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_depth=3, min_samples_leaf=20,
                                         l2_regularization=1.0, max_features=0.8, early_stopping=False,
                                         random_state=seed)


def to_values(instances: pd.DataFrame, predicted_log_ratio: np.ndarray,
              ranges: dict[str, tuple[float, float]]) -> np.ndarray:
    """Convert predicted log ratios to values in the indicator's units, clipped to its plausible range."""
    values = instances["last"].to_numpy() * np.exp(predicted_log_ratio)
    lo = instances["concept"].map(lambda c: ranges[c][0]).to_numpy()
    hi = instances["concept"].map(lambda c: ranges[c][1]).to_numpy()
    return np.clip(values, lo, hi)
