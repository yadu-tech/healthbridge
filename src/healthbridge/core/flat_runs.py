"""Flat-run flags for modelled mortality series.

A modelled mortality estimate that repeats exactly for several consecutive years is not a
measurement: it comes from the producer's model, for example a constant fit through a conflict
period (Alkema and New 2014 document this for Somalia) or a projection with no new data. Such a
stretch says nothing about the trend, and trend statistics computed across it are misleading.

Design choices
--------------
* Flag, never reject, and never drop: the flag is informational. It is not used by the analysis
  panel's exclusion rule or by the fault-injection scoring.
* Only series where exact repetition is not expected are checked. Immunization coverage is
  published as whole percentages and repeats legitimately, so it is excluded.
* Series published to one decimal place or less are not assessed. The World Bank publishes mortality
  to one decimal, and a slowly changing low-mortality series (Seychelles neonatal mortality, 8.7 for
  twelve years) repeats through rounding alone, so a repeat there cannot be told from a model feature.
  This is the same principle as the outlier check's minimum number of neighbours. WHO and UNICEF
  publish full precision, and the same estimate is checked there.
* Years must be consecutive: a gap ends a run. Equal means equal to within 1e-9.
* ``MIN_RUN`` is a judgement: four years is long enough to rule out a chance repeat in a full-precision
  series and shorter than the runs found in the data (Somalia 1995 to 2004, South Sudan 2019 to 2024).
"""
from __future__ import annotations

from itertools import groupby

import psycopg

FLAG = "flat_run"
MIN_RUN = 4
TOLERANCE = 1e-9
# Concepts whose published values are modelled continuous estimates, where a repeat is a model feature.
FLAT_RUN_CONCEPTS = ("under5_mortality", "neonatal_mortality", "maternal_mortality_ratio")


def flat_run_mask(years: list[int], values: list[float], min_run: int = MIN_RUN,
                  tolerance: float = TOLERANCE) -> list[bool]:
    """True for every point inside a run of at least ``min_run`` consecutive equal years.

    ``years`` must be ascending. A missing year or a different value ends the run.
    """
    mask = [False] * len(values)
    start = 0
    for i in range(1, len(values) + 1):
        continues = (i < len(values) and years[i] == years[i - 1] + 1
                     and abs(values[i] - values[start]) <= tolerance)
        if not continues:
            if i - start >= min_run:
                for j in range(start, i):
                    mask[j] = True
            start = i
    return mask


def is_precision_limited(values: list[float], step: float = 0.1, tolerance: float = 1e-6) -> bool:
    """True when every value is a multiple of ``step``: repeats then cannot be told from rounding."""
    return all(abs(v / step - round(v / step)) <= tolerance / step for v in values)


def flag_flat_runs(conn: psycopg.Connection, run_id: str, *, min_run: int = MIN_RUN,
                   concepts: tuple[str, ...] = FLAT_RUN_CONCEPTS) -> int:
    """Append the ``flat_run`` flag to selected headline rows inside a flat run; returns the count."""
    rows = conn.execute(
        "SELECT f.observation_id, f.source_key, f.country_key, f.indicator_key, f.year, f.value"
        " FROM core.fact_observation f JOIN core.dim_indicator i USING (indicator_key)"
        " WHERE f.run_id = %s AND f.is_headline AND f.is_selected AND i.concept = ANY(%s)"
        " ORDER BY f.source_key, f.country_key, f.indicator_key, f.year", (run_id, list(concepts)),
    ).fetchall()
    flagged: list[int] = []
    for _, group in groupby(rows, key=lambda r: (r[1], r[2], r[3])):
        series = list(group)
        if is_precision_limited([r[5] for r in series]):
            continue
        mask = flat_run_mask([r[4] for r in series], [r[5] for r in series], min_run)
        flagged += [row[0] for row, hit in zip(series, mask, strict=True) if hit]
    if flagged:
        conn.execute(
            "UPDATE core.fact_observation SET quality_flags = array_append(quality_flags, %s)"
            " WHERE observation_id = ANY(%s)", (FLAG, flagged),
        )
    return len(flagged)
