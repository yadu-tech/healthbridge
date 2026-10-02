"""Schema validation for raw source files, run before anything is loaded.

Each source has a set of *required* fields (a missing one makes the file unusable, so loading
stops) and a set of *known* fields. A field outside the known set is reported but not fatal:
sources add columns over time, and that should be visible without breaking the pipeline.

The known sets were taken from the snapshot of 2026-10-01 and are the schema contract that
later snapshots are checked against.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field

WHO_REQUIRED = ("IndicatorCode", "SpatialDim", "TimeDim", "NumericValue", "Value")
WHO_KNOWN = frozenset(WHO_REQUIRED) | {
    "Id", "SpatialDimType", "ParentLocationCode", "ParentLocation", "TimeDimType", "Dim1Type",
    "Dim1", "Dim2Type", "Dim2", "Dim3Type", "Dim3", "DataSourceDimType", "DataSourceDim", "Low",
    "High", "Comments", "Date", "TimeDimensionValue", "TimeDimensionBegin", "TimeDimensionEnd",
}

WORLDBANK_REQUIRED = ("indicator.id", "country.id", "countryiso3code", "date", "value")
WORLDBANK_KNOWN = frozenset(WORLDBANK_REQUIRED) | {
    "indicator.value", "country.value", "unit", "obs_status", "decimal",
}

UNICEF_REQUIRED = ("REF_AREA", "INDICATOR", "TIME_PERIOD", "OBS_VALUE")
UNICEF_KNOWN = frozenset(UNICEF_REQUIRED) | {
    "SEX", "AGE", "AGE_AT_BIRTH", "WEALTH_QUINTILE", "RESIDENCE", "MOTHER_EDUCATION",
    "DATA_SOURCE", "UNIT_MULTIPLIER", "UNIT_MEASURE", "SERIES_FOOTNOTE", "SUB_SECTOR",
    "SOWC_FLAG_A", "DATA_SOURCE_PRIORITY", "OBS_STATUS", "OBS_CONF", "LOWER_BOUND", "UPPER_BOUND",
    "WGTD_SAMPL_SIZE", "UNWGTD_SAMPL_SIZE", "OBS_FOOTNOTE", "SOURCE_LINK", "TIME_PERIOD_METHOD",
    "COVERAGE_TIME", "COUNTRY_NOTES", "REF_PERIOD", "VACCINE", "FREQ_COLL", "MATERNAL_EDU_LVL",
    "HEAD_OF_HOUSE", "REPORTING_LVL", "INDICATOR_METADATA", "CUSTODIAN", "PUBLICATION_DATE",
    "STD_ERR",
}


class SchemaError(RuntimeError):
    """A raw file lacks fields the pipeline requires."""


@dataclass
class SchemaReport:
    missing: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing


def _flatten(row: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in row.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            keys |= _flatten(value, f"{name}.")
        else:
            keys.add(name)
    return keys


def _report(rows_keys: list[set[str]], required: tuple[str, ...], known: frozenset[str]) -> SchemaReport:
    if not rows_keys:
        return SchemaReport()  # an empty file carries no schema evidence either way
    present_in_all = set.intersection(*rows_keys)
    present_anywhere = set.union(*rows_keys)
    return SchemaReport(
        missing=sorted(set(required) - present_in_all),
        unexpected=sorted(present_anywhere - known),
    )


def check_schema(source: str, content: bytes) -> SchemaReport:
    """Check one raw file; never raises on malformed content (it is reported as missing)."""
    try:
        if source == "who":
            rows = json.loads(content)["value"]
            return _report([set(r) for r in rows], WHO_REQUIRED, WHO_KNOWN)
        if source == "worldbank":
            rows = json.loads(content)[1] or []
            return _report([_flatten(r) for r in rows], WORLDBANK_REQUIRED, WORLDBANK_KNOWN)
        if source == "unicef":
            header = next(csv.reader(io.StringIO(content.decode("utf-8-sig"))), [])
            return _report([set(header)] if header else [], UNICEF_REQUIRED, UNICEF_KNOWN)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return SchemaReport(missing=["<unreadable structure>"])
    raise ValueError(f"unknown source {source!r}")
