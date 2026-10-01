"""Temporal outlier flags for the default analysis surface.

A point is compared with its nearest neighbours in the same (source, country, indicator)
series, using a robust modified z-score (median and MAD; Iglewicz and Hoaglin) with a relative
floor so that very smooth series do not flag tiny deviations.

Design choices
--------------
* Flag, never reject. A real shock (conflict, epidemic) can look like an outlier, so flagged
  rows stay in the data with ``quality_flags`` containing ``temporal_outlier``.
* The parameters were fixed before the fault-injection experiment and not tuned on it. The
  threshold is the conventional 3.5; the relative floor (20%) is a judgement.
* Series with fewer than ``min_neighbors`` neighbours are not assessed: with so little
  context a deviation cannot be told apart from a trend.
"""
from __future__ import annotations

import statistics
from itertools import groupby

import psycopg

OUTLIER_Z = 3.5          # modified z-score threshold, on the 1.4826 * MAD scale
OUTLIER_REL_FLOOR = 0.20  # deviation must also exceed this share of the neighbour median
OUTLIER_ABS_FLOOR = 0.1   # and this absolute amount (guards near-zero series)
NEIGHBORS_EACH_SIDE = 2
MIN_NEIGHBORS = 3
FLAG = "temporal_outlier"


def is_outlier(value: float, neighbors: list[float], z: float, rel_floor: float,
               abs_floor: float, min_neighbors: int) -> bool:
    if len(neighbors) < min_neighbors:
        return False
    median = statistics.median(neighbors)
    mad = statistics.median(abs(x - median) for x in neighbors)
    threshold = max(z * 1.4826 * mad, rel_floor * abs(median), abs_floor)
    return abs(value - median) > threshold


def flag_temporal_outliers(
    conn: psycopg.Connection,
    run_id: str,
    *,
    z: float = OUTLIER_Z,
    rel_floor: float = OUTLIER_REL_FLOOR,
    abs_floor: float = OUTLIER_ABS_FLOOR,
    neighbors: int = NEIGHBORS_EACH_SIDE,
    min_neighbors: int = MIN_NEIGHBORS,
) -> int:
    """Append the ``temporal_outlier`` flag to outlying selected headline rows; returns the count."""
    rows = conn.execute(
        "SELECT observation_id, source_key, country_key, indicator_key, year, value"
        " FROM core.fact_observation WHERE run_id = %s AND is_headline AND is_selected"
        " ORDER BY source_key, country_key, indicator_key, year", (run_id,)
    ).fetchall()
    flagged: list[int] = []
    for _, group in groupby(rows, key=lambda r: (r[1], r[2], r[3])):
        series = list(group)
        values = [r[5] for r in series]
        for i, row in enumerate(series):
            context = values[max(0, i - neighbors):i] + values[i + 1:i + 1 + neighbors]
            if is_outlier(row[5], context, z, rel_floor, abs_floor, min_neighbors):
                flagged.append(row[0])
    if flagged:
        conn.execute(
            "UPDATE core.fact_observation SET quality_flags = array_append(quality_flags, %s)"
            " WHERE observation_id = ANY(%s)", (FLAG, flagged),
        )
    return len(flagged)
