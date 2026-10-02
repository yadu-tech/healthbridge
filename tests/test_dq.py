"""Data-quality baseline tests on a hand-built snapshot with a known number of seeded defects."""
import json

import pytest

from healthbridge.dq.baseline import compute_baseline
from healthbridge.dq.report import render_report
from healthbridge.ingest.snapshot import SnapshotWriter
from healthbridge.ingest.sources import RawPage
from healthbridge.staging.load import load_snapshot

CONCEPT = "dtp3_coverage"  # plausible range 0..100


def who_row(iso, year, value, *, text="auto", dim1="SEX_BTSX", low=None, high=None, region="AFR"):
    if text == "auto":
        text = None if value is None else str(value)
    return {"Id": 1, "IndicatorCode": "X", "SpatialDimType": "COUNTRY", "SpatialDim": iso,
            "ParentLocationCode": region, "TimeDimType": "YEAR", "TimeDim": year,
            "Dim1Type": "SEX", "Dim1": dim1, "Value": text, "NumericValue": value,
            "Low": low, "High": high}


# Eight WHO rows. Seeded defects, by row:
#  3 out of range (150 > 100)        4 missing value            5 unparseable text 'abc'
#  6 outside own bounds (70 > 60)    7 invalid country 'XXX'    8 exact duplicate of row 1
#  1, 2, 8 share (NGA, 2019) -> grain ambiguity (3 rows)
WHO_ROWS = [
    who_row("NGA", 2019, 90), who_row("NGA", 2019, 90, dim1="SEX_MLE"),
    who_row("KEN", 2019, 150), who_row("KEN", 2020, None),
    who_row("GHA", 2020, None, text="abc"), who_row("GHA", 2021, 70, low=50, high=60),
    who_row("XXX", 2019, 50, region="EMR"), who_row("NGA", 2019, 90),
]
WB_ROWS = [{"indicator": {"id": "SH.X", "value": "x"}, "country": {"id": "NG", "value": "Nigeria"},
            "countryiso3code": "NGA", "date": "2019", "value": 90, "unit": "", "obs_status": "",
            "decimal": 0}]
UNICEF_CSV = (
    "REF_AREA,INDICATOR,SEX,TIME_PERIOD,OBS_VALUE\n"
    "NGA,IM_DTP3,_T,2019,88\n"
    "NGA,IM_DTP3,F,2019,60\n"
    "KEN,IM_DTP3,_T,2016-2017,50\n"  # period is not a plain year
)


def build_snapshot(root, run_id):
    writer = SnapshotWriter(
        root, run_id=run_id,
        scope={"countries": ["NGA", "KEN", "GHA"], "years": [2019, 2021]},
    )
    writer.write_pages([
        RawPage("who", "X", CONCEPT, 1, "u", 200, "application/json",
                json.dumps({"value": WHO_ROWS}).encode(), "json"),
        RawPage("worldbank", "SH.X", CONCEPT, 1, "u", 200, "application/json",
                json.dumps([{"page": 1, "pages": 1}, WB_ROWS]).encode(), "json"),
        RawPage("unicef", "F.IM_DTP3", CONCEPT, 1, "u", 200, "text/csv", UNICEF_CSV.encode(), "csv"),
    ])
    writer.finalize()
    return writer.run_dir


def metrics_of(conn, dq_run_id):
    rows = conn.execute(
        "SELECT scope, source, concept, dimension, metric, value, numerator, denominator, details"
        " FROM dq.metric WHERE dq_run_id = %s", (dq_run_id,)).fetchall()
    return {(r[0], r[1], r[2], r[3], r[4]): r for r in rows}


@pytest.fixture
def dq_run(pg_conn, tmp_path):
    run_dir = build_snapshot(tmp_path, "dq1")
    load_snapshot(pg_conn, run_dir)
    return pg_conn, run_dir, compute_baseline(pg_conn, run_dir)


@pytest.mark.integration
def test_who_row_level_checks_count_exactly_the_seeded_defects(dq_run):
    conn, _, dq_id = dq_run
    m = metrics_of(conn, dq_id)

    def num(dimension, metric):
        row = m[("series", "who", CONCEPT, dimension, metric)]
        assert row[7] == 8  # denominator = records received
        return row[6]

    assert m[("series", "who", CONCEPT, "completeness", "records_received")][5] == 8
    assert num("completeness", "missing_rate") == 2        # rows 4, 5
    assert num("validity", "unparseable_rate") == 1        # row 5
    assert num("validity", "out_of_range_rate") == 1       # row 3
    assert num("validity", "bounds_violation_rate") == 1   # row 6
    assert num("validity", "invalid_country_rate") == 1    # row 7
    assert num("validity", "invalid_year_rate") == 0
    assert num("validity", "invalid_row_rate") == 4        # rows 3, 5, 6, 7 (no double counting)
    assert num("uniqueness", "exact_duplicate_rate") == 1  # row 8 duplicates row 1
    assert num("consistency", "noncanonical_period_rate") == 0


@pytest.mark.integration
def test_grain_ambiguity_coverage_and_scores(dq_run):
    conn, _, dq_id = dq_run
    m = metrics_of(conn, dq_id)
    ambiguous = m[("series", "who", CONCEPT, "uniqueness", "grain_ambiguity_rate")]
    assert (ambiguous[6], ambiguous[7]) == (3, 8)           # rows 1, 2, 8 share (NGA, 2019)
    cells = m[("series", "who", CONCEPT, "uniqueness", "ambiguous_cell_rate")]
    assert (cells[6], cells[7]) == (1, 6)                    # 1 of 6 country-year cells
    cover = m[("series", "who", CONCEPT, "completeness", "grid_coverage")]
    assert (cover[6], cover[7]) == (3, 9)                    # 3 valued cells of 3 countries x 3 years

    assert m[("source", "who", "", "score", "completeness")][5] == pytest.approx(0.75)
    assert m[("source", "who", "", "score", "validity")][5] == pytest.approx(0.5)
    assert m[("source", "who", "", "score", "uniqueness")][5] == pytest.approx(0.625)
    assert m[("source", "who", "", "score", "consistency")][5] == pytest.approx(1.0)
    composite = m[("source", "who", "", "score", "composite")]
    assert composite[5] == pytest.approx((0.75 + 0.5 + 0.625 + 1.0) / 4)
    assert composite[8]["leave_one_out_min"] <= composite[5] <= composite[8]["leave_one_out_max"]


@pytest.mark.integration
def test_unicef_noncanonical_period_and_naive_join_fanout(dq_run):
    conn, _, dq_id = dq_run
    m = metrics_of(conn, dq_id)
    period = m[("series", "unicef", CONCEPT, "consistency", "noncanonical_period_rate")]
    assert (period[6], period[7]) == (1, 3)                  # '2016-2017'
    # Only (NGA, 2019) is in all three sources: WHO 3 rows x World Bank 1 x UNICEF 2 = 6.
    fan = m[("concept", "", CONCEPT, "integration", "naive_join_fanout")]
    assert (fan[6], fan[7]) == (6, 1)
    # Of those 6 joined rows, the 3 pairing UNICEF's 60 with 90/90/90 differ by more than 10%.
    conflict = m[("concept", "", CONCEPT, "integration", "naive_join_conflict_rate")]
    assert (conflict[6], conflict[7]) == (3, 6)


@pytest.mark.integration
def test_vocabulary_region_and_schema_metrics(dq_run):
    conn, _, dq_id = dq_run
    m = metrics_of(conn, dq_id)
    assert m[("source", "who", "", "integration", "distinct_sex_values")][8] == ["SEX_BTSX", "SEX_MLE"]
    assert m[("source", "unicef", "", "integration", "distinct_sex_values")][8] == ["F", "_T"]
    region = m[("source", "who", "", "integration", "countries_outside_who_afr_region")]
    assert (region[6], region[7]) == (1, 4)                  # XXX is the only non-AFR country
    assert m[("source", "unicef", "", "integration", "distinct_column_sets")][5] == 1


@pytest.mark.integration
def test_baseline_is_reproducible(dq_run):
    conn, run_dir, first = dq_run
    second = compute_baseline(conn, run_dir)
    a, b = metrics_of(conn, first), metrics_of(conn, second)
    a.pop(("overall", "", "", "completeness", "processing_seconds"))
    b.pop(("overall", "", "", "completeness", "processing_seconds"))
    assert {k: v[5:] for k, v in a.items()} == {k: v[5:] for k, v in b.items()}


@pytest.mark.integration
def test_baseline_requires_a_loaded_snapshot(pg_conn, tmp_path):
    run_dir = build_snapshot(tmp_path, "not_loaded")
    with pytest.raises(RuntimeError, match="not loaded"):
        compute_baseline(pg_conn, run_dir)


@pytest.mark.integration
def test_report_renders_the_stored_numbers(dq_run):
    conn, _, dq_id = dq_run
    report = render_report(conn, dq_id)
    assert "snapshot `dq1`" in report
    assert "| who | dtp3_coverage | 8 |" in report
    assert "6.0x" in report            # naive join fan-out
    assert "SEX_BTSX" in report and "EMR" in report
