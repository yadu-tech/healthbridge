"""Parse raw snapshot files into typed staging rows.

Pure functions (bytes in, list of dicts out) so they are testable without a database.
Parsing only *types* values; it does not clean, filter, deduplicate or harmonize them.
A value that cannot be parsed becomes None, while its raw text is kept alongside, so the
data-quality layer can still count it.
"""
from __future__ import annotations

import csv
import io
import json

WHO_COLUMNS = (
    "who_id", "indicator_code", "spatial_dim_type", "spatial_dim", "parent_location_code",
    "parent_location", "time_dim_type", "time_dim", "dim1_type", "dim1", "dim2_type", "dim2",
    "dim3_type", "dim3", "data_source_dim_type", "data_source_dim", "value_text",
    "numeric_value", "low", "high", "comments", "source_modified_at",
)
WORLDBANK_COLUMNS = (
    "indicator_id", "indicator_name", "country_id", "country_name", "country_iso3",
    "date_text", "year", "value", "unit", "obs_status", "decimals",
)
UNICEF_COLUMNS = (
    "ref_area", "indicator", "sex", "age", "wealth_quintile", "residence", "data_source",
    "unit_measure", "time_period", "year", "value_text", "obs_value", "lower_bound",
    "upper_bound", "obs_status", "obs_conf", "series_footnote", "obs_footnote", "extra",
)
# CSV columns promoted to real columns; every other column goes into the `extra` jsonb.
_UNICEF_DIRECT = {
    "REF_AREA": "ref_area", "INDICATOR": "indicator", "SEX": "sex", "AGE": "age",
    "WEALTH_QUINTILE": "wealth_quintile", "RESIDENCE": "residence", "DATA_SOURCE": "data_source",
    "UNIT_MEASURE": "unit_measure", "TIME_PERIOD": "time_period", "OBS_STATUS": "obs_status",
    "OBS_CONF": "obs_conf", "SERIES_FOOTNOTE": "series_footnote", "OBS_FOOTNOTE": "obs_footnote",
}
_UNICEF_CONSUMED = set(_UNICEF_DIRECT) | {"OBS_VALUE", "LOWER_BOUND", "UPPER_BOUND"}


def _text(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _float(value) -> float | None:
    try:
        return float(value) if _text(value) is not None else None
    except (TypeError, ValueError):
        return None


def _int(value) -> int | None:
    number = _float(value)
    return int(number) if number is not None and number == int(number) else None


def _year(period: str | None) -> int | None:
    """Year from the leading four digits of a period such as '2017' or '2016-2017'."""
    return int(period[:4]) if period and period[:4].isdigit() else None


def parse_who(content: bytes) -> list[dict]:
    rows = json.loads(content)["value"]
    return [{
        "who_id": _int(r.get("Id")),
        "indicator_code": _text(r.get("IndicatorCode")),
        "spatial_dim_type": _text(r.get("SpatialDimType")),
        "spatial_dim": _text(r.get("SpatialDim")),
        "parent_location_code": _text(r.get("ParentLocationCode")),
        "parent_location": _text(r.get("ParentLocation")),
        "time_dim_type": _text(r.get("TimeDimType")),
        "time_dim": _int(r.get("TimeDim")),
        "dim1_type": _text(r.get("Dim1Type")), "dim1": _text(r.get("Dim1")),
        "dim2_type": _text(r.get("Dim2Type")), "dim2": _text(r.get("Dim2")),
        "dim3_type": _text(r.get("Dim3Type")), "dim3": _text(r.get("Dim3")),
        "data_source_dim_type": _text(r.get("DataSourceDimType")),
        "data_source_dim": _text(r.get("DataSourceDim")),
        "value_text": _text(r.get("Value")),
        "numeric_value": _float(r.get("NumericValue")),
        "low": _float(r.get("Low")),
        "high": _float(r.get("High")),
        "comments": _text(r.get("Comments")),
        "source_modified_at": _text(r.get("Date")),
    } for r in rows]


def parse_worldbank(content: bytes) -> list[dict]:
    payload = json.loads(content)
    rows = payload[1] or []
    return [{
        "indicator_id": _text((r.get("indicator") or {}).get("id")),
        "indicator_name": _text((r.get("indicator") or {}).get("value")),
        "country_id": _text((r.get("country") or {}).get("id")),
        "country_name": _text((r.get("country") or {}).get("value")),
        "country_iso3": _text(r.get("countryiso3code")),
        "date_text": _text(r.get("date")),
        "year": _int(r.get("date")),
        "value": _float(r.get("value")),
        "unit": _text(r.get("unit")),
        "obs_status": _text(r.get("obs_status")),
        "decimals": _int(r.get("decimal")),
    } for r in rows]


def parse_unicef(content: bytes) -> list[dict]:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    out = []
    for r in reader:
        row = {target: _text(r.get(source)) for source, target in _UNICEF_DIRECT.items()}
        row["year"] = _year(row["time_period"])
        row["value_text"] = _text(r.get("OBS_VALUE"))
        row["obs_value"] = _float(r.get("OBS_VALUE"))
        row["lower_bound"] = _float(r.get("LOWER_BOUND"))
        row["upper_bound"] = _float(r.get("UPPER_BOUND"))
        extra = {k: _text(v) for k, v in r.items() if k not in _UNICEF_CONSUMED and _text(v)}
        row["extra"] = json.dumps(extra, ensure_ascii=False, sort_keys=True) if extra else None
        out.append(row)
    return out
