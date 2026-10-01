import hashlib
import json

import pytest

from healthbridge.db import MigrationError, migrate
from healthbridge.ingest.snapshot import SnapshotWriter
from healthbridge.ingest.sources import RawPage
from healthbridge.staging import parse
from healthbridge.staging.load import SnapshotIntegrityError, load_snapshot

WHO_JSON = json.dumps({"value": [
    {"Id": 7, "IndicatorCode": "X", "SpatialDimType": "COUNTRY", "SpatialDim": "NGA",
     "ParentLocationCode": "AFR", "ParentLocation": "Africa", "TimeDimType": "YEAR",
     "TimeDim": 2019, "Dim1Type": "SEX", "Dim1": "SEX_BTSX", "Dim2Type": None, "Dim2": None,
     "Dim3Type": "WEALTHQUINTILE", "Dim3": "WEALTHQUINTILE_TOTL", "Value": "97.5 [81.9-116.0]",
     "NumericValue": 97.5, "Low": 81.9, "High": 116.0, "Comments": None,
     "Date": "2026-08-05T14:54:28.66+02:00"},
    {"Id": 8, "IndicatorCode": "X", "SpatialDim": "KEN", "TimeDim": 2020, "NumericValue": None,
     "Value": ""},
]}).encode()

WB_JSON = json.dumps([{"page": 1, "pages": 1}, [
    {"indicator": {"id": "SH.X", "value": "Some indicator"}, "country": {"id": "NG", "value": "Nigeria"},
     "countryiso3code": "NGA", "date": "2019", "value": 12.5, "unit": "", "obs_status": "", "decimal": 1},
    {"indicator": {"id": "SH.X", "value": "Some indicator"},
     "country": {"id": "KE", "value": "Kenya"}, "countryiso3code": "KEN", "date": "2020",
     "value": None, "unit": "", "obs_status": "", "decimal": 0},
]]).encode()

UNICEF_CSV = (
    b"REF_AREA,INDICATOR,SEX,TIME_PERIOD,OBS_VALUE,LOWER_BOUND,VACCINE,UNIT_MEASURE\n"
    b"NGA,IM_DTP3,_T,2019,57.0,,DTP3,PCNT\n"
    b"KEN,IM_DTP3,_T,2016-2017,n/a,,DTP3,PCNT\n"
)


# --- parsers (no database) --------------------------------------------------------------

def test_parse_who_types_values_and_keeps_missing_as_none():
    rows = parse.parse_who(WHO_JSON)
    assert rows[0]["spatial_dim"] == "NGA" and rows[0]["time_dim"] == 2019
    assert rows[0]["numeric_value"] == 97.5 and rows[0]["value_text"].startswith("97.5")
    assert rows[1]["numeric_value"] is None and rows[1]["value_text"] is None  # '' -> NULL
    assert set(rows[0]) == set(parse.WHO_COLUMNS)


def test_parse_worldbank_keeps_null_rows_and_iso3():
    rows = parse.parse_worldbank(WB_JSON)
    assert [r["country_iso3"] for r in rows] == ["NGA", "KEN"]
    assert rows[0]["value"] == 12.5 and rows[1]["value"] is None
    assert rows[0]["year"] == 2019 and rows[0]["unit"] is None  # '' -> NULL
    assert set(rows[0]) == set(parse.WORLDBANK_COLUMNS)


def test_parse_unicef_unparseable_value_keeps_raw_text_and_extra_columns():
    rows = parse.parse_unicef(UNICEF_CSV)
    assert rows[0]["obs_value"] == 57.0 and rows[0]["year"] == 2019
    assert json.loads(rows[0]["extra"]) == {"VACCINE": "DTP3"}  # dimension not in the common set
    assert rows[1]["obs_value"] is None and rows[1]["value_text"] == "n/a"
    assert rows[1]["year"] == 2016 and rows[1]["time_period"] == "2016-2017"
    assert set(rows[0]) == set(parse.UNICEF_COLUMNS)


# --- migrations and loading (PostgreSQL) -------------------------------------------------

@pytest.mark.integration
def test_migrate_is_idempotent_and_detects_edited_migrations(pg_conn, tmp_path):
    assert migrate(pg_conn) == []  # already applied by the fixture
    (tmp_path / "001_schemas.sql").write_text("SELECT 1;")  # same version, different content
    with pytest.raises(MigrationError):
        migrate(pg_conn, tmp_path)


def _snapshot(tmp_path, run_id="run1"):
    writer = SnapshotWriter(tmp_path, scope={}, run_id=run_id)
    pages = [
        RawPage("who", "X", "demo", 1, "u", 200, "application/json", WHO_JSON, "json"),
        RawPage("worldbank", "SH.X", "demo", 1, "u", 200, "application/json", WB_JSON, "json"),
        RawPage("unicef", "F.IM_DTP3", "demo", 1, "u", 200, "text/csv", UNICEF_CSV, "csv"),
    ]
    writer.write_pages(pages)
    writer.finalize()
    return writer.run_dir


@pytest.mark.integration
def test_load_snapshot_loads_rows_with_lineage_and_is_idempotent(pg_conn, tmp_path):
    run_dir = _snapshot(tmp_path)
    assert load_snapshot(pg_conn, run_dir) == {"who": 2, "worldbank": 2, "unicef": 2}
    assert load_snapshot(pg_conn, run_dir) == {"who": 2, "worldbank": 2, "unicef": 2}  # reload

    count = pg_conn.execute(
        "SELECT count(*) FROM staging.who_observation WHERE run_id = 'run1'"
    ).fetchone()[0]
    assert count == 2  # replaced, not duplicated
    row = pg_conn.execute(
        "SELECT run_id, source_file, row_num, spatial_dim, numeric_value"
        " FROM staging.who_observation WHERE run_id = 'run1' ORDER BY row_num LIMIT 1"
    ).fetchone()
    assert row == ("run1", "who/X/page-0001.json", 1, "NGA", 97.5)
    log = pg_conn.execute(
        "SELECT source, rows_loaded, length(sha256) FROM staging.load_log"
        " WHERE run_id = 'run1' ORDER BY source"
    ).fetchall()
    assert log == [("unicef", 2, 64), ("who", 2, 64), ("worldbank", 2, 64)]
    extra = pg_conn.execute(
        "SELECT extra->>'VACCINE' FROM staging.unicef_observation"
        " WHERE run_id = 'run1' AND row_num = 1"
    ).fetchone()[0]
    assert extra == "DTP3"


@pytest.mark.integration
def test_load_refuses_a_tampered_snapshot_and_leaves_existing_rows_alone(pg_conn, tmp_path):
    run_dir = _snapshot(tmp_path, run_id="run2")
    load_snapshot(pg_conn, run_dir)
    before = pg_conn.execute(
        "SELECT count(*) FROM staging.who_observation WHERE run_id = 'run2'"
    ).fetchone()[0]
    (run_dir / "who/X/page-0001.json").write_bytes(b'{"value": []}')
    with pytest.raises(SnapshotIntegrityError):
        load_snapshot(pg_conn, run_dir)
    after = pg_conn.execute(
        "SELECT count(*) FROM staging.who_observation WHERE run_id = 'run2'"
    ).fetchone()[0]
    assert before == after


@pytest.mark.integration
def test_failed_load_rolls_back_completely(pg_conn, tmp_path):
    run_dir = _snapshot(tmp_path, run_id="run3")
    # Valid checksums, but unparseable content: the failure happens mid-load, after deletes.
    bad = json.loads((run_dir / "manifest.json").read_text())
    path = run_dir / "worldbank/SH.X/page-0001.json"
    path.write_bytes(b"not json")
    for entry in bad["files"]:
        if entry["source"] == "worldbank":
            entry["sha256"] = hashlib.sha256(b"not json").hexdigest()
    (run_dir / "manifest.json").write_text(json.dumps(bad))
    with pytest.raises(json.JSONDecodeError):
        load_snapshot(pg_conn, run_dir)
    rows = pg_conn.execute(
        "SELECT count(*) FROM staging.who_observation WHERE run_id = 'run3'"
    ).fetchone()[0]
    assert rows == 0  # the who rows copied before the failure were rolled back
