"""Tests for the data-quality ablation: the naive loader, scoring against the clean truth, the paired
statistics, the extended fault injector, and a small end-to-end run through the real pipeline."""
import json
import math
import random

import numpy as np
import pandas as pd
import pytest

from healthbridge.core.build import build_core
from healthbridge.experiments import faults as fx
from healthbridge.ingest.snapshot import SnapshotWriter
from healthbridge.ingest.sources import RawPage
from healthbridge.marts.build import build_marts
from healthbridge.ml import ablation as ab
from healthbridge.ml import dataset as ds
from healthbridge.reference import indicator_ranges
from healthbridge.staging.load import load_snapshot
from tests.test_marts import snapshot as marts_snapshot
from tests.test_ml_models import synthetic_panel

RANGES = indicator_ranges()
COUNTRIES = ["NGA", "KEN", "GHA", "ETH", "UGA", "TZA", "MLI", "SEN", "CMR", "COD", "ZMB", "ZWE",
             "MWI", "MOZ", "AGO", "BEN", "BFA", "BDI", "RWA", "SDN", "TUN", "DZA", "EGY", "MAR"]
BASE = {"under5_mortality": 120.0, "maternal_mortality_ratio": 500.0, "dtp3_coverage": 40.0,
        "measles_mcv1_coverage": 45.0}


def who_row(iso, year, value, *, dim1=None, dim3=None):
    return {"Id": 1, "IndicatorCode": "X", "SpatialDimType": "COUNTRY", "SpatialDim": iso,
            "ParentLocationCode": "AFR", "ParentLocation": "Africa", "TimeDimType": "YEAR", "TimeDim": year,
            "Dim1Type": "SEX" if dim1 else None, "Dim1": dim1, "Dim3Type": "WEALTHQUINTILE" if dim3 else None,
            "Dim3": dim3, "Value": None if value is None else str(value), "NumericValue": value}


def write_snapshot(root, run_id, pages, countries, years):
    writer = SnapshotWriter(root, run_id=run_id, scope={"countries": countries, "years": list(years)})
    writer.write_pages(pages)
    writer.finalize()
    return writer.run_dir


def who_page(concept, rows, series=None):
    return RawPage("who", series or f"WHO_{concept}", concept, 1, "u", 200, "application/json",
                   json.dumps({"value": rows}).encode(), "json")


# --- the naive loader ----------------------------------------------------------------------

def test_naive_loader_applies_exactly_its_stated_rules(tmp_path):
    total = {"dim1": "SEX_BTSX", "dim3": "WEALTHQUINTILE_TOTL"}
    rows = [
        who_row("NGA", 2010, 100.0, **total),                                     # kept
        who_row("NGA", 2010, 90.0, dim1="SEX_MLE", dim3="WEALTHQUINTILE_TOTL"),    # a breakdown: dropped
        who_row("NGA", 2011, 95.0, dim1="Both sexes", dim3="WEALTHQUINTILE_TOTL"),  # unknown code: dropped
        who_row("nga", 2012, 94.0, **total),                                      # exact ISO3 join only: dropped
        who_row("KEN", 2010, None, **total),                                      # null: dropped
        who_row("KEN", 2011, -5.0, **total),                                      # no range check: kept
        who_row("KEN", 2012, 80.0, **total) | {"TimeDim": "19"},                  # no year check: kept as year 19
        who_row("GHA", 2010, 7.0, **total), who_row("GHA", 2010, 9.0, **total),    # a later duplicate overwrites
    ]
    other = RawPage("worldbank", "SH.X", "under5_mortality", 1, "u", 200, "application/json",
                    json.dumps([{"page": 1, "pages": 1}, []]).encode(), "json")
    ignored = who_page("stunting_prevalence", [who_row("NGA", 2010, 40.0)])       # not a modelled indicator
    snap = write_snapshot(tmp_path, "naive1", [who_page("under5_mortality", rows), other, ignored],
                          ["NGA", "KEN", "GHA"], (1990, 2024))
    panel = ab.naive_panel(snap, {"NGA": "Western Africa", "KEN": "Eastern Africa", "GHA": "Western Africa"})
    got = {(r.iso3, r.year): r.value for r in panel.itertuples()}
    assert got == {("NGA", 2010): 100.0, ("KEN", 2011): -5.0, ("KEN", 19): 80.0, ("GHA", 2010): 9.0}
    assert set(panel["concept"]) == {"under5_mortality"}
    assert panel.set_index("iso3").loc["NGA", "un_subregion"] == "Western Africa"


# --- scoring is against the clean truth ----------------------------------------------------

def _real_panel(n=24):
    panel = synthetic_panel(n)
    mapping = dict(zip(sorted(panel["iso3"].unique()), COUNTRIES, strict=False))
    return panel.assign(iso3=panel["iso3"].map(mapping))


def _truth_and_keys(panel, cutoffs):
    inst = ds.build_instances(panel, RANGES)
    truth = {(r.concept, r.iso3, r.year): r.value for r in panel.itertuples()}
    keys = {(r.concept, r.iso3, r.origin_year, r.horizon)
            for r in inst[inst["origin_year"].isin(cutoffs)].itertuples()}
    return inst, truth, keys


def test_scores_use_the_clean_truth_even_when_the_variants_own_target_is_corrupt():
    clean = _real_panel()
    inst, truth, keys = _truth_and_keys(clean, (2008,))
    reference = ab.evaluate_variant(inst, truth, keys, RANGES, cutoffs=(2008,), gbm_seeds=(0,))
    # corrupt a TARGET year (2012) that no cut-off-2008 training or feature touches
    victim = (clean["concept"] == "under5_mortality") & (clean["iso3"] == "NGA") & (clean["year"] == 2012)
    bad = clean.copy()
    bad.loc[victim, "value"] *= 2.5
    bad_inst = ds.build_instances(bad, RANGES)
    assert not np.allclose(bad_inst["actual"], inst["actual"])                      # the variant's own label did change
    scored = ab.evaluate_variant(bad_inst, truth, keys, RANGES, cutoffs=(2008,), gbm_seeds=(0,))
    pd.testing.assert_frame_equal(reference, scored)                            # but scoring did not


def test_a_variant_that_lost_data_has_lower_coverage_and_is_scored_on_what_it_can_forecast():
    clean = _real_panel()
    inst, truth, keys = _truth_and_keys(clean, (2008,))
    full = ab.evaluate_variant(inst, truth, keys, RANGES, cutoffs=(2008,), gbm_seeds=(0,))
    missing = clean[clean["iso3"] != "NGA"]
    partial = ab.evaluate_variant(ds.build_instances(missing, RANGES), truth, keys, RANGES,
                                  cutoffs=(2008,), gbm_seeds=(0,))
    assert len(partial) < len(full) and "NGA" not in set(partial["iso3"])
    assert {tuple(r) for r in partial[["concept", "iso3", "origin_year", "horizon"]].to_numpy()} <= keys


# --- the paired comparison -----------------------------------------------------------------

def _frames(out, rate, seed, ape_clean, ape_naive, ape_pipeline, countries=12, per=6):
    (out / "frames").mkdir(parents=True, exist_ok=True)
    rows = [{"concept": "under5_mortality", "iso3": f"C{c:02d}", "origin_year": 2008 + j, "horizon": 4, "cutoff": 2008}
            for c in range(countries) for j in range(per)]
    base = pd.DataFrame(rows)
    tag = f"r{round(rate * 100):02d}_s{seed}"
    for name, ape, path in (("clean", ape_clean, out / "frames" / "clean.csv.gz"),
                            ("naive", ape_naive, out / "frames" / f"{tag}_naive.csv.gz"),
                            ("pipeline", ape_pipeline, out / "frames" / f"{tag}_pipeline.csv.gz")):
        ab._save(base.assign(ape_baseline=ape, ape_gbm=ape), path)
    meta = {"runs": {tag: {}}, "rates": [rate], "seeds": [seed], "clean_run_id": "x", "n_clean_keys": len(base),
            "code_version": "test", "clean_dropped_share": 0.01}
    (out / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def test_pipeline_that_degrades_less_is_reported_as_such(tmp_path):
    _frames(tmp_path, 0.10, 1, ape_clean=0.04, ape_naive=0.10, ape_pipeline=0.05)
    summary = ab.build_summary(tmp_path, resamples=100)
    row = summary["rows"].query("concept == 'all' and model == 'gbm'").iloc[0]
    assert row["naive"]["median"] == pytest.approx(0.06) and row["pipeline"]["median"] == pytest.approx(0.01)
    assert row["naive_minus_pipeline"]["low"] > 0
    report = ab.render_ablation_report(summary)
    assert "**yes**" in report and "Pipeline degrades less" in report


def test_a_pipeline_that_degrades_more_is_reported_as_worse(tmp_path):
    _frames(tmp_path, 0.10, 1, ape_clean=0.04, ape_naive=0.05, ape_pipeline=0.09)
    report = ab.render_ablation_report(ab.build_summary(tmp_path, resamples=100))
    assert "**worse**" in report                       # the protocol says this outcome is reported as found


def test_no_difference_is_not_called_a_win(tmp_path):
    _frames(tmp_path, 0.10, 1, ape_clean=0.04, ape_naive=0.07, ape_pipeline=0.07)
    report = ab.render_ablation_report(ab.build_summary(tmp_path, resamples=100))
    assert "not shown" in report and "**yes**" not in report


def test_pre_registered_ablation_values_are_pinned():
    assert ab.RATES == (0.01, 0.03, 0.10) and ab.SEEDS == (1, 2, 3) and ab.GBM_SEEDS == (0, 1, 2)
    assert ab.BASELINE_FOR == {"under5_mortality": "linear_trend_5", "maternal_mortality_ratio": "linear_trend_5",
                               "dtp3_coverage": "last_value", "measles_mcv1_coverage": "last_value"}
    assert ab.DECISIVE_BUCKET == "4-5" and fx.ABLATION_MAGNITUDES == (0.10, 0.25, 0.50, 1.0, 3.0)


# --- the extended injector and the end-to-end run (database) ---------------------------------

def _clean_pipeline(conn, root, run_id="abl_clean"):
    """A synthetic clean snapshot with the four modelled indicators, taken through the real pipeline."""
    panel = _real_panel()
    pages = []
    for concept in ds.CONCEPTS:
        rows = [who_row(r.iso3, int(r.year), float(r.value), **(
            {"dim1": "SEX_BTSX", "dim3": "WEALTHQUINTILE_TOTL"} if concept == "under5_mortality" else {}))
            for r in panel[panel["concept"] == concept].itertuples()]
        pages.append(who_page(concept, rows))
    snap = write_snapshot(root, run_id, pages, COUNTRIES, (1990, 2024))
    load_snapshot(conn, snap)
    build_core(conn, snap)
    build_marts(conn, snap)
    return snap


@pytest.mark.integration
def test_load_panel_run_drops_flagged_values_only_when_asked(pg_conn, tmp_path):
    run_dir = marts_snapshot(tmp_path)
    load_snapshot(pg_conn, run_dir)
    build_core(pg_conn, run_dir, dependence_min_shared=1)
    build_marts(pg_conn, run_dir, min_countries=5)
    kept = ab.load_panel_run(pg_conn, "mart1", drop_flagged=False)
    dropped = ab.load_panel_run(pg_conn, "mart1", drop_flagged=True)
    assert len(kept) - len(dropped) == 1                                       # Morocco's 2008 DTP3 spike
    flagged = kept.merge(dropped, how="left", indicator=True).query("_merge == 'left_only'")
    assert list(flagged[["iso3", "year"]].itertuples(index=False, name=None)) == [("MAR", 2008)]
    assert {"concept", "iso3", "un_subregion", "year", "value"} <= set(kept.columns)


@pytest.mark.integration
def test_mixed_injection_hits_rows_once_and_mixes_fault_types(pg_conn, tmp_path):
    snap = _clean_pipeline(pg_conn, tmp_path)
    store = fx.RawStore(snap)
    pool = [p for p in fx.build_pool(pg_conn, "abl_clean") if p.concept in ds.CONCEPTS]
    faults = fx.inject_mixed(store, store.working_copy(), pool, random.Random(3), 0.10)
    originals = [(f.file, f.row_num) for f in faults if f.fault_type != "duplicate"]
    assert len(originals) == len(set(originals))                                # no row is corrupted twice
    kinds = {f.fault_type for f in faults}
    assert "value_change" in kinds and {"missing_value", "out_of_range", "duplicate"} <= kinds
    share = len(faults) / len(fx.clean_universe(pool))
    assert 0.07 < share < 0.13                                                  # close to the requested rate


@pytest.mark.integration
def test_unisolated_magnitude_faults_may_share_a_series_but_isolated_ones_may_not(pg_conn, tmp_path):
    snap = _clean_pipeline(pg_conn, tmp_path, "abl_clean2")
    store = fx.RawStore(snap)
    pool = [p for p in fx.build_pool(pg_conn, "abl_clean2") if p.concept in ds.CONCEPTS]
    series_of = {(p.file, p.row_num): p.series for p in pool}
    free = fx.inject_magnitude(store, store.working_copy(), pool, random.Random(1), 400, (0.5,), isolate=False)
    isolated = fx.inject_magnitude(store, store.working_copy(), pool, random.Random(1), 400, (0.5,), isolate=True)
    count = lambda faults: pd.Series([series_of[(f.file, f.row_num)] for f in faults]).astype(str).value_counts()
    assert count(free).max() > 1 and count(isolated).max() == 1
    exclude = {(f.file, f.row_num) for f in free[:50]}
    again = fx.inject_magnitude(store, store.working_copy(), pool, random.Random(1), 400, (0.5,), isolate=False,
                                exclude=exclude)
    assert not exclude & {(f.file, f.row_num) for f in again}


@pytest.mark.integration
def test_end_to_end_ablation_runs_resumes_and_reports(pg_conn, tmp_path):
    snap = _clean_pipeline(pg_conn, tmp_path, "abl_e2e")
    pg_conn.commit()
    out = tmp_path / "ablation"
    meta_path = ab.run_ablation(pg_conn, snap, out, rates=(0.10,), seeds=(1,), resume=False)
    meta = json.loads(meta_path.read_text())
    run = meta["runs"]["r10_s1"]
    assert run["n_faults"] > 100 and run["naive_rows"] > 0 and 0 <= run["pipeline_dropped_share"] < 0.2
    for name in ("clean", "r10_s1_naive", "r10_s1_pipeline"):
        assert (out / "frames" / f"{name}.csv.gz").exists()
    # the experiment cleaned up after itself, in staging, core and the marts
    left = pg_conn.execute("SELECT count(*) FROM core.fact_observation WHERE run_id = 'ab_r10_s1'").fetchone()[0]
    left += pg_conn.execute("SELECT count(*) FROM marts.country_indicator_year WHERE run_id = 'ab_r10_s1'").fetchone()[0]
    assert left == 0 and not (out / "snapshots" / "ab_r10_s1").exists()
    # resuming keeps finished work
    stamp = (out / "frames" / "r10_s1_pipeline.csv.gz").stat().st_mtime_ns
    ab.run_ablation(pg_conn, snap, out, rates=(0.10,), seeds=(1,), resume=True)
    assert (out / "frames" / "r10_s1_pipeline.csv.gz").stat().st_mtime_ns == stamp
    summary = ab.build_summary(out, resamples=50)
    row = summary["rows"].query("concept == 'all' and model == 'gbm'").iloc[0]
    assert row["n"] > 50 and all(math.isfinite(row[k]) for k in ("ape_clean", "ape_naive", "ape_pipeline"))
    assert 0 < summary["coverage"]["coverage"].min() <= 1
    assert "Primary outcome" in ab.render_ablation_report(summary)
    assert np.isfinite(summary["seed_spread"]["gbm_naive"]).all()
