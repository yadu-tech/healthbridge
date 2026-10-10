"""Tests for the flat-run flag: a modelled mortality estimate that repeats for several years."""
import pytest

from healthbridge.core.build import build_core
from healthbridge.core.flat_runs import (
    FLAT_RUN_CONCEPTS,
    MIN_RUN,
    flag_flat_runs,
    flat_run_mask,
    is_precision_limited,
)
from healthbridge.marts.build import build_marts
from healthbridge.staging.load import load_snapshot
from tests.test_checks import q, snapshot, wb_row, who_row


def test_pre_set_parameters_are_pinned():
    assert MIN_RUN == 4
    assert FLAT_RUN_CONCEPTS == ("under5_mortality", "neonatal_mortality", "maternal_mortality_ratio")


# --- the rule ------------------------------------------------------------------------------

def test_a_run_of_exactly_the_minimum_length_is_flagged_and_one_year_shorter_is_not():
    years = list(range(2000, 2008))
    assert flat_run_mask(years, [90, 80, 70, 70, 70, 70, 60, 50]) == [False, False, True, True, True, True,
                                                                      False, False]
    assert not any(flat_run_mask(years, [90, 80, 70, 70, 70, 60, 50, 40]))      # three equal years


def test_a_missing_year_ends_the_run():
    # same value in 2000, 2001, 2003, 2004: not four consecutive years
    assert not any(flat_run_mask([2000, 2001, 2003, 2004], [5.0, 5.0, 5.0, 5.0]))


def test_equality_is_to_within_a_tiny_tolerance_not_approximate():
    years = [2000, 2001, 2002, 2003]
    assert all(flat_run_mask(years, [100.0, 100.0 + 1e-12, 100.0, 100.0 - 1e-12]))
    assert not any(flat_run_mask(years, [100.0, 100.1, 100.0, 100.0]))           # 0.1% apart is not equal


def test_two_runs_in_one_series_are_flagged_separately_and_the_value_between_is_not():
    years = list(range(2000, 2012))
    values = [4.0] * 4 + [3.5] + [3.0] * 5 + [2.0, 1.0]
    assert flat_run_mask(years, values) == [True] * 4 + [False] + [True] * 5 + [False, False]


def test_a_series_that_never_repeats_and_degenerate_inputs_are_not_flagged():
    assert not any(flat_run_mask([2000, 2001, 2002, 2003], [4.0, 3.9, 3.8, 3.7]))
    assert flat_run_mask([], []) == []
    assert flat_run_mask([2000], [1.0]) == [False]


def test_series_published_to_one_decimal_are_precision_limited_and_full_precision_ones_are_not():
    assert is_precision_limited([8.7, 8.7, 8.8, 9.0])           # World Bank style: a repeat may be rounding
    assert is_precision_limited([96.0, 97.0, 98.0])             # whole numbers
    assert not is_precision_limited([173.230022603, 173.230022603])
    assert not is_precision_limited([8.7, 8.7, 8.7312])         # one finer value makes the series assessable


# --- through the pipeline (database) ---------------------------------------------------------

# 2010..2020 at full precision (as WHO and UNICEF publish), flat 2012-2016
FLAT_SERIES = [100.4231, 98.1177, 96.3529, 96.3529, 96.3529, 96.3529, 96.3529, 90.2204, 88.0731, 86.4416, 84.1902]


def _build(pg_conn, tmp_path, run_id, *, who=True):
    if who:
        run_dir = snapshot(tmp_path, run_id, who_rows=[who_row("NGA", 2010 + i, v)
                                                        for i, v in enumerate(FLAT_SERIES)], years=(2010, 2020))
    else:   # the same shape from the World Bank, whose values are one decimal place
        rows = [wb_row("NGA", "NG", 2010 + i, round(v, 1)) for i, v in enumerate(FLAT_SERIES)]
        run_dir = snapshot(tmp_path, run_id, wb_rows=rows, years=(2010, 2020))
    load_snapshot(pg_conn, run_dir)
    build_core(pg_conn, run_dir)
    return run_dir


@pytest.mark.integration
def test_only_the_flat_stretch_is_flagged_and_nothing_is_rejected(pg_conn, tmp_path):
    _build(pg_conn, tmp_path, "flat1")
    flagged = q(pg_conn, "SELECT year FROM core.fact_observation WHERE run_id = 'flat1'"
                         " AND 'flat_run' = ANY(quality_flags) ORDER BY year")
    assert [y for (y,) in flagged] == [2012, 2013, 2014, 2015, 2016]
    # informational only: all 11 rows stay on the default surface and none is treated as an outlier
    assert q(pg_conn, "SELECT count(*) FROM core.v_headline_observation WHERE run_id = 'flat1'")[0][0] == 11
    assert q(pg_conn, "SELECT count(*) FROM core.fact_observation WHERE run_id = 'flat1'"
                      " AND 'temporal_outlier' = ANY(quality_flags)")[0][0] == 0
    log = q(pg_conn, "SELECT parameters FROM core.build_log WHERE run_id = 'flat1'")[0][0]
    assert log["alerts"]["flat_run_rows"] == 5 and log["flat_run_min_years"] == 4


@pytest.mark.integration
def test_a_one_decimal_series_is_not_assessed_even_when_it_repeats(pg_conn, tmp_path):
    _build(pg_conn, tmp_path, "flat4", who=False)
    values = q(pg_conn, "SELECT value FROM core.fact_observation WHERE run_id = 'flat4' ORDER BY year")
    assert len({v for (v,) in values}) < len(values)            # the repeat is really there
    assert q(pg_conn, "SELECT count(*) FROM core.fact_observation WHERE run_id = 'flat4'"
                      " AND 'flat_run' = ANY(quality_flags)")[0][0] == 0


@pytest.mark.integration
def test_concepts_outside_the_checked_set_are_not_assessed(pg_conn, tmp_path):
    _build(pg_conn, tmp_path, "flat2")
    # the same run, asked about a coverage concept: this series is under-5 mortality, so nothing matches
    assert flag_flat_runs(pg_conn, "flat2", concepts=("dtp3_coverage",)) == 0
    # a rebuild starts from a clean fact table, so each row still carries the flag exactly once
    build_core(pg_conn, tmp_path / "flat2")
    n = q(pg_conn, "SELECT count(*) FROM core.fact_observation WHERE run_id = 'flat2'"
                   " AND array_position(quality_flags, 'flat_run') IS NOT NULL")[0][0]
    flags = q(pg_conn, "SELECT max(cardinality(array_positions(quality_flags, 'flat_run')))"
                       " FROM core.fact_observation WHERE run_id = 'flat2'")[0][0]
    assert n == 5 and flags == 1


@pytest.mark.integration
def test_the_flag_reaches_the_panel_and_the_trust_table(pg_conn, tmp_path):
    run_dir = _build(pg_conn, tmp_path, "flat3")
    build_marts(pg_conn, run_dir, min_countries=1)
    panel = q(pg_conn, "SELECT year, flat_run FROM marts.country_indicator_year WHERE run_id = 'flat3'"
                       " ORDER BY year")
    assert [y for y, flat in panel if flat] == [2012, 2013, 2014, 2015, 2016]
    share = q(pg_conn, "SELECT share_flat_run FROM marts.data_trust WHERE run_id = 'flat3'")[0][0]
    assert share == pytest.approx(5 / 11)
