"""Ingestion tests. All HTTP is faked, so they run offline and deterministically."""
import json

import pytest
import requests

from healthbridge.config import AFRICA_ISO3, CONCEPTS, Concept
from healthbridge.ingest.runner import run_ingestion
from healthbridge.ingest.snapshot import SnapshotWriter, sha256_bytes, verify_snapshot
from healthbridge.ingest.sources import (
    SOURCES,
    RawPage,
    SourceError,
    fetch_unicef,
    fetch_who,
    fetch_worldbank,
)

YEARS = (2000, 2020)
CONCEPT = Concept("demo", "WHO_DEMO", "WB.DEMO", "FLOW", "FLOW_DEMO")


class FakeResponse:
    def __init__(self, payload=None, *, text=None, status=200, url="https://example.test/x",
                 content_type="application/json"):
        self.status_code = status
        self.url = url
        self.content = (text if text is not None else json.dumps(payload)).encode()
        self.headers = {"Content-Type": content_type}

    @property
    def text(self):
        return self.content.decode()

    def json(self):
        return json.loads(self.content)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class FakeSession:
    """Returns queued responses in order, or calls handler(url, params)."""

    def __init__(self, responses=None, handler=None):
        self.queue = list(responses or [])
        self.handler = handler
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self.handler(url, params) if self.handler else self.queue.pop(0)


# --- registry ---------------------------------------------------------------------------

def test_africa_scope_is_54_unique_iso3_codes():
    assert len(AFRICA_ISO3) == 54
    assert len(set(AFRICA_ISO3)) == 54
    assert all(len(code) == 3 and code.isupper() for code in AFRICA_ISO3)


def test_every_concept_is_mapped_in_all_sources():
    assert len({c.name for c in CONCEPTS}) == len(CONCEPTS)
    for c in CONCEPTS:
        assert all([c.who_code, c.worldbank_code, c.unicef_flow, c.unicef_indicator])


# --- fetchers ---------------------------------------------------------------------------

def test_who_follows_next_link_and_keeps_bytes_unchanged():
    first = FakeResponse({"value": [{"Id": 1}], "@odata.nextLink": "https://example.test/next"})
    second = FakeResponse({"value": [{"Id": 2}]})
    session = FakeSession([first, second])
    pages = list(fetch_who(session, CONCEPT, YEARS))
    assert [p.page for p in pages] == [1, 2]
    assert pages[0].content == first.content  # raw bytes are untouched
    assert session.calls[1][0] == "https://example.test/next"
    assert session.calls[1][1] is None  # nextLink already carries the query


def test_who_filter_uses_in_operator_and_year_bounds():
    session = FakeSession([FakeResponse({"value": []})])
    list(fetch_who(session, CONCEPT, YEARS))
    odata_filter = session.calls[0][1]["$filter"]
    assert "SpatialDim in (" in odata_filter
    assert " or " not in odata_filter  # the GHO API rejects >100 filter nodes
    assert "TimeDim ge 2000" in odata_filter and "TimeDim le 2020" in odata_filter
    assert all(f"'{iso}'" in odata_filter for iso in AFRICA_ISO3)


def test_who_payload_without_value_is_an_error():
    with pytest.raises(SourceError):
        list(fetch_who(FakeSession([FakeResponse({"oops": 1})]), CONCEPT, YEARS))


def test_worldbank_pages_until_last_page():
    p1 = FakeResponse([{"page": 1, "pages": 2}, [{"v": 1}]])
    p2 = FakeResponse([{"page": 2, "pages": 2}, [{"v": 2}]])
    pages = list(fetch_worldbank(FakeSession([p1, p2]), CONCEPT, YEARS))
    assert [p.page for p in pages] == [1, 2]


def test_worldbank_error_message_with_http_200_is_detected():
    err = FakeResponse([{"message": [{"id": "120", "value": "Invalid value"}]}])
    with pytest.raises(SourceError):
        list(fetch_worldbank(FakeSession([err]), CONCEPT, YEARS))


def test_unicef_returns_csv_and_requests_codes_not_labels():
    csv_text = "REF_AREA,INDICATOR,TIME_PERIOD,OBS_VALUE\nNGA,FLOW_DEMO,2019,12.5\n"
    session = FakeSession([FakeResponse(text=csv_text, content_type="text/csv")])
    (page,) = list(fetch_unicef(session, CONCEPT, YEARS))
    assert page.extension == "csv" and page.series == "FLOW.FLOW_DEMO"
    assert "labels" not in session.calls[0][1]  # default output carries ISO3 codes


def test_unicef_unexpected_header_is_an_error():
    session = FakeSession([FakeResponse(text="<html>maintenance</html>", content_type="text/html")])
    with pytest.raises(SourceError):
        list(fetch_unicef(session, CONCEPT, YEARS))


# --- snapshots --------------------------------------------------------------------------

def _page(content=b'{"value": []}', page=1):
    return RawPage("who", "CODE", "demo", page, "https://example.test", 200,
                   "application/json", content, "json")


def test_snapshot_records_checksums_and_verifies(tmp_path):
    writer = SnapshotWriter(tmp_path, scope={"k": 1}, run_id="r1")
    writer.write_pages([_page(page=1), _page(b"{}", page=2)])
    writer.finalize()
    manifest = json.loads((tmp_path / "r1" / "manifest.json").read_text())
    assert manifest["files"][0]["sha256"] == sha256_bytes(b'{"value": []}')
    assert manifest["files"][0]["path"] == "who/CODE/page-0001.json"
    assert verify_snapshot(tmp_path / "r1") == []


def test_verify_detects_tampering_missing_and_extra_files(tmp_path):
    writer = SnapshotWriter(tmp_path, scope={}, run_id="r1")
    writer.write_pages([_page(page=1), _page(b"{}", page=2)])
    writer.finalize()
    run = tmp_path / "r1"
    (run / "who/CODE/page-0001.json").write_bytes(b"tampered")
    (run / "who/CODE/page-0002.json").unlink()
    (run / "who/CODE/extra.json").write_bytes(b"{}")
    problems = verify_snapshot(run)
    assert "checksum mismatch: who/CODE/page-0001.json" in problems
    assert "missing file: who/CODE/page-0002.json" in problems
    assert "unlisted file: who/CODE/extra.json" in problems


def test_snapshots_are_immutable(tmp_path):
    SnapshotWriter(tmp_path, scope={}, run_id="r1")
    with pytest.raises(FileExistsError):
        SnapshotWriter(tmp_path, scope={}, run_id="r1")


# --- runner -----------------------------------------------------------------------------

def test_runner_records_failures_and_continues(tmp_path):
    good, bad = Concept("good", "G", "g", "F", "F_G"), Concept("bad", "B", "b", "F", "F_B")

    def handler(url, params):
        if url.endswith("/B"):
            return FakeResponse(status=500)
        return FakeResponse({"value": [1]})

    writer = run_ingestion(tmp_path, sources=["who"], concepts=[bad, good], years=YEARS,
                           session=FakeSession(handler=handler), run_id="run", pause_seconds=0)
    assert [f["concept"] for f in writer.failures] == ["bad"]
    assert [f["concept"] for f in writer.files] == ["good"]
    manifest = json.loads((tmp_path / "run" / "manifest.json").read_text())
    assert manifest["failures"][0]["source"] == "who"
    assert manifest["scope"]["years"] == [2000, 2020]
    assert verify_snapshot(tmp_path / "run") == []  # no half-written series left behind


def test_runner_rejects_unknown_source(tmp_path):
    with pytest.raises(ValueError, match="unknown source"):
        run_ingestion(tmp_path, sources=["nope"], session=FakeSession(), pause_seconds=0)


def test_source_registry_matches_expected_names():
    assert set(SOURCES) == {"who", "worldbank", "unicef"}
