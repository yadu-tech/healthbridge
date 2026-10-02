"""Tests for the fault-injection harness: the measurement tool needs its own verification."""
import json
import random

import pytest

from healthbridge.core.build import build_core
from healthbridge.experiments import evaluate as ev
from healthbridge.experiments import faults as fx
from healthbridge.experiments.run import purge_run, run_pass
from healthbridge.ingest.snapshot import SnapshotWriter, verify_snapshot
from healthbridge.ingest.sources import RawPage
from healthbridge.staging.load import load_snapshot
from healthbridge.staging.schema import SchemaReport

CONCEPT = "dtp3_coverage"


# --- pure logic --------------------------------------------------------------------------

def test_changed_value_stays_in_range_and_differs():
    rng = random.Random(1)
    for magnitude in (0.02, 0.5, 1.0, 9.0):
        for value in (3.0, 50.0, 97.0):
            new = fx.changed_value(rng, value, magnitude, 0, 100)
            assert new is None or (0 <= new <= 100 and new != value)
    assert fx.changed_value(random.Random(1), 99.0, 9.0, 0, 100) is not None  # downward is possible
    assert fx.changed_value(random.Random(1), 0.0, 0.5, 0, 100) is None        # nothing to scale


def test_out_of_range_values_are_outside_the_range():
    rng = random.Random(2)
    for _ in range(50):
        new = fx.out_of_range_value(rng, 40.0, 0, 100)
        assert new < 0 or new > 100


def test_wilson_interval():
    assert ev.wilson(0, 0) == (0.0, 0.0)
    lo, hi = ev.wilson(10, 10)
    assert hi == 1.0 and 0.65 < lo < 0.75          # 10/10 is not "certainly 100%"
    lo, hi = ev.wilson(50, 100)
    assert lo < 0.5 < hi and abs((0.5 - lo) - (hi - 0.5)) < 0.01


def _fault(**kw):
    base = {"fault_type": "missing_value", "variant": "x", "source": "who", "file": "f", "row_num": 1,
            "concept": CONCEPT, "iso3": "NGA", "year": 2015, "orig_value": 50.0}
    return fx.Fault(**{**base, **kw})


def _fact(**kw):
    return {"iso3": "NGA", "year": 2015, "value": 50.0, "flags": [], "resolution": "exact", **kw}


def test_classification_covers_every_outcome():
    f = _fault()
    out = ev.Outcomes(rejected={("f", 1): "no_value"}, facts={}, recon={})
    assert ev.classify(f, out) == ["rejected:no_value"]
    assert ev.classify(f, ev.Outcomes({}, {}, {})) == ["lost"]
    assert ev.classify(f, ev.Outcomes({}, {("f", 1): _fact()}, {})) == ["recovered"]
    assert ev.classify(f, ev.Outcomes({}, {("f", 1): _fact(value=99.0)}, {})) == ["silent"]
    assert ev.classify(f, ev.Outcomes({}, {("f", 1): _fact(iso3="KEN")}, {})) == ["silent"]  # wrong country
    flagged = ev.Outcomes({}, {("f", 1): _fact(value=99.0, flags=["temporal_outlier"])},
                          {(CONCEPT, "NGA", 2015): {"conflict": False, "within_group": True}})
    assert ev.classify(f, flagged) == ["flagged:temporal_outlier", "flagged:within_group_disagreement"]
    cross = ev.Outcomes({}, {("f", 1): _fact(value=99.0)},
                        {(CONCEPT, "NGA", 2015): {"conflict": True, "within_group": False}})
    assert ev.classify(f, cross) == ["flagged:conflict"]


def test_schema_classification():
    f = _fault(fault_type="schema_rename_required", row_num=0)
    assert ev.classify_schema(f, {"f": SchemaReport(missing=["x"])}) == ["schema:error"]
    assert ev.classify_schema(f, {"f": SchemaReport(unexpected=["y"])}) == ["schema:warned"]
    assert ev.classify_schema(f, {"f": SchemaReport()}) == ["silent"]


def test_naive_loader_outcomes():
    canonical = {"NGA", "KEN"}
    rows = {"f": [{"spatial_dim": "NGA", "time_dim": 2015, "numeric_value": 50.0},
                  {"spatial_dim": "Nigeria", "time_dim": 2015, "numeric_value": 50.0},
                  {"spatial_dim": "NGA", "time_dim": None, "numeric_value": 50.0},
                  {"spatial_dim": "NGA", "time_dim": 2015, "numeric_value": 500.0},
                  {"spatial_dim": "NGA", "time_dim": 19, "numeric_value": 50.0}]}
    out = lambda n, **kw: ev.naive_outcome(_fault(row_num=n, **kw), rows, canonical)
    assert out(1) == "accepted_correct"
    assert out(2) == "dropped"                    # exact ISO3 join fails on a name
    assert out(3) == "dropped"                    # no year
    assert out(4) == "accepted_wrong"             # a wrong value goes straight in
    assert out(5) == "accepted_wrong"             # year 19 is accepted without a range check
    assert out(1, fault_type="duplicate") == "accepted_wrong"   # counted twice
    assert out(1, fault_type="categorical_invalid") is None


def test_false_positives_exclude_touched_rows_and_count_cells_once():
    def pool_row(n, **kw):
        return fx.PoolRow("who", "f", n, CONCEPT, kw.get("iso3", "NGA"), kw.get("year", 2015), 50.0, (),
                          False, 4, "g", 2, ("s",))
    universe = [pool_row(1), pool_row(2), pool_row(3, iso3="KEN"), pool_row(4, iso3="GHA")]
    outcomes = ev.Outcomes(
        rejected={("f", 2): "exact_duplicate"},
        facts={("f", 1): _fact(flags=["temporal_outlier"]), ("f", 3): _fact(iso3="KEN"),
               ("f", 4): _fact(iso3="GHA", flags=["temporal_outlier"])},
        recon={(CONCEPT, "KEN", 2015): {"conflict": False, "within_group": True},
               (CONCEPT, "GHA", 2015): {"conflict": False, "within_group": True}})
    injected = [_fault(row_num=4, iso3="GHA")]   # row 4 and the GHA cell were touched on purpose
    counts = ev.false_positives(universe, outcomes, injected)
    assert counts == {"flagged:temporal_outlier": 1,           # row 1; row 4 is injected
                      "rejected:exact_duplicate": 1,           # row 2
                      "flagged:within_group_disagreement": 1}  # KEN cell; the GHA cell is injected


# --- injection against a small synthetic snapshot ----------------------------------------

def who_row(iso, year, value):
    return {"Id": 1, "IndicatorCode": "X", "SpatialDimType": "COUNTRY", "SpatialDim": iso,
            "ParentLocationCode": "AFR", "ParentLocation": "Africa", "TimeDimType": "YEAR",
            "TimeDim": year, "Dim1Type": None, "Dim1": None, "Dim2Type": None, "Dim2": None,
            "Dim3Type": None, "Dim3": None, "Value": str(value), "NumericValue": value,
            "Low": value - 1, "High": value + 1}


def wb_row(iso, iso2, year, value):
    return {"indicator": {"id": "SH.X", "value": "x"}, "country": {"id": iso2, "value": iso},
            "countryiso3code": iso, "date": str(year), "value": value, "unit": "", "obs_status": "",
            "decimal": 0}


YEARS = list(range(2005, 2017))                 # 12 years
COUNTRIES = [("NGA", "NG", 0), ("KEN", "KE", 10), ("GHA", "GH", 15)]


def clean_snapshot(root, run_id="exp_clean"):
    who, wb, uni = [], [], []
    for iso3, iso2, offset in COUNTRIES:
        for i, year in enumerate(YEARS):
            value = round(40 + 2.5 * i + offset, 3)
            who.append(who_row(iso3, year, value))
            wb.append(wb_row(iso3, iso2, year, round(value * 1.001, 4)))
            uni.append(f"{iso3},IM_DTP3,_T,M12T23,UNICEF,{year},{round(value * 1.0005, 4)}")
    csv_text = "REF_AREA,INDICATOR,SEX,AGE,DATA_SOURCE,TIME_PERIOD,OBS_VALUE\n" + "\n".join(uni) + "\n"
    writer = SnapshotWriter(root, run_id=run_id, scope={
        "countries": [c[0] for c in COUNTRIES], "years": [YEARS[0], YEARS[-1]]})
    writer.write_pages([
        RawPage("who", "WHS4_100", CONCEPT, 1, "u", 200, "application/json",
                json.dumps({"value": who}).encode(), "json"),
        RawPage("worldbank", "SH.IMM.IDPT", CONCEPT, 1, "u", 200, "application/json",
                json.dumps([{"page": 1, "pages": 1}, wb]).encode(), "json"),
        RawPage("unicef", "IMMUNISATION.IM_DTP3", CONCEPT, 1, "u", 200, "text/csv",
                csv_text.encode(), "csv"),
    ])
    writer.finalize()
    return writer.run_dir


@pytest.fixture
def clean(pg_conn, tmp_path):
    run_dir = clean_snapshot(tmp_path)
    load_snapshot(pg_conn, run_dir)
    build_core(pg_conn, run_dir)
    store = fx.RawStore(run_dir)
    pool = fx.build_pool(pg_conn, "exp_clean")
    return pg_conn, store, pool, fx.clean_universe(pool), tmp_path


@pytest.mark.integration
def test_pool_has_context_and_evidence_groups(clean):
    _, _, pool, _universe, _ = clean
    assert len(pool) == 108                                  # 3 sources x 3 countries x 12 years
    assert {p.group for p in pool} == {"unicef+who+worldbank"}   # the three agree: one evidence group
    assert all(p.cell_members == 3 for p in pool)
    interior = [p for p in pool if p.year == 2010]
    assert all(p.n_neighbors == 4 for p in interior)
    assert min(p.n_neighbors for p in pool) == 2             # the series ends have only two neighbours


@pytest.mark.integration
def test_validity_injection_invariants(clean):
    _, store, pool, _universe, tmp = clean
    parsed = store.working_copy()
    faults = fx.inject_validity(store, parsed, pool, random.Random(7), 3)
    assert {f.fault_type for f in faults} >= set(fx.VALIDITY_FAULTS) - {"categorical_invalid"}
    keys = [(f.file, f.row_num) for f in faults]
    assert len(keys) == len(set(keys))                       # at most one fault per row
    originals = {(f.file, f.origin_row_num) for f in faults if f.origin_row_num}
    assert not originals & set(keys)                         # a copied row is not itself corrupted

    snapshot = fx.write_snapshot(store, parsed, "exp_v", tmp / "snaps")
    assert verify_snapshot(snapshot) == []                   # the corrupted copy is a valid snapshot
    clean_hashes = {e["path"]: e["sha256"] for e in store.manifest["files"]}
    assert clean_hashes == {e["path"]: e["sha256"] for e in json.loads(
        (store.run_dir / "manifest.json").read_text())["files"]}   # the clean snapshot is untouched
    assert verify_snapshot(store.run_dir) == []


@pytest.mark.integration
def test_injection_is_deterministic_per_seed(clean):
    _, store, pool, _, _ = clean
    logs = []
    for seed in (3, 3, 4):
        faults = fx.inject_validity(store, store.working_copy(), pool, random.Random(seed), 3)
        logs.append([f.to_json() for f in faults])
    assert logs[0] == logs[1]
    assert logs[0] != logs[2]


@pytest.mark.integration
def test_magnitude_injection_touches_one_row_per_series_and_cell(clean):
    _, store, pool, _, _ = clean
    faults = fx.inject_magnitude(store, store.working_copy(), pool, random.Random(5), 2)
    series = [(f.source, f.iso3, f.concept) for f in faults]
    cells = [(f.concept, f.iso3, f.year) for f in faults]
    assert len(series) == len(set(series)) and len(cells) == len(set(cells))
    assert all(f.orig_value is not None and float(f.corrupted) != f.orig_value for f in faults)


@pytest.mark.integration
def test_end_to_end_validity_pass_through_the_real_pipeline(clean):
    conn, store, pool, universe, tmp = clean
    record = run_pass(conn, store, pool, universe, tmp / "exp", "validity", 1, 3, 2, keep=False)
    assert record["n_faults"] >= 20
    wrong = [r for r in record["results"] if not r["correct"]]
    assert wrong == []                                       # rule-based checks catch all of these
    by_type = {r["fault_type"] for r in record["results"]}
    assert {"duplicate", "out_of_range", "country_recoverable", "country_invalid"} <= by_type
    recovered = [r for r in record["results"] if r["fault_type"] == "country_recoverable"]
    assert all(r["labels"] == ["recovered"] for r in recovered)
    # the experiment cleans up after itself
    left = conn.execute("SELECT count(*) FROM core.fact_observation WHERE run_id = 'fi_validity_s1'").fetchone()
    assert left[0] == 0
    assert record["false_positives"].get("rejected:exact_duplicate", 0) == 0


@pytest.mark.integration
def test_end_to_end_magnitude_and_schema_passes(clean):
    conn, store, pool, universe, tmp = clean
    magnitude = run_pass(conn, store, pool, universe, tmp / "exp", "magnitude", 1, 3, 2, keep=False)
    by_level = {}
    for r in magnitude["results"]:
        by_level.setdefault(r["variant"], []).append(r)
    assert all(r["correct"] for level in ("100%", "300%", "900%") for r in by_level.get(level, []))
    assert all(r["silent"] for r in by_level.get("2%", []))   # below every threshold: undetectable
    schema = run_pass(conn, store, pool, universe, tmp / "exp", "schema", 1, 3, 2, keep=False)
    # three files, so one distinct file per schema fault type
    assert [r["correct"] for r in schema["results"]] == [True] * 3
    assert sorted(r["labels"][0] for r in schema["results"]) == ["schema:error"] * 2 + ["schema:warned"]


@pytest.mark.integration
def test_purge_run_removes_everything_for_a_run(clean):
    conn, *_ = clean
    purge_run(conn, "exp_clean")
    for table in ("core.fact_observation", "core.fact_reconciled", "staging.who_observation",
                  "staging.load_log"):
        assert conn.execute(f"SELECT count(*) FROM {table} WHERE run_id = 'exp_clean'").fetchone()[0] == 0
