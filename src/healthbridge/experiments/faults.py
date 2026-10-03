"""Seeded fault injection into a copy of a raw snapshot, with a ground-truth log.

The clean snapshot is never modified. A corrupted copy is written as a *valid* snapshot (new run
id, recomputed checksums), so the real pipeline runs on it unchanged. Every injected fault is
recorded with its exact location and the outcomes that would count as correct handling.

Rules that keep the measurement honest
--------------------------------------
* Faults are injected only into rows that were clean in the unmodified run: selected headline
  rows with no quality flag, in cells with no pre-existing source conflict.
* Each row receives at most one fault, so outcomes can be attributed.
* Rows are only modified in place or appended, never removed, so a row keeps its (file, row
  number) identity in the corrupted snapshot.
* Value corruptions (magnitude pass) touch at most one row per series and one row per cell, so
  one injected error cannot contaminate the context used to judge another.
* Value-changing faults also scale the source's own uncertainty bounds, mimicking a unit or
  scale error; otherwise the bounds check would trivially expose them.
"""
from __future__ import annotations

import copy
import csv
import io
import json
import random
from dataclasses import asdict, dataclass, field
from itertools import groupby
from pathlib import Path

import psycopg

from healthbridge.ingest.snapshot import MANIFEST_NAME, SnapshotWriter
from healthbridge.ingest.sources import RawPage
from healthbridge.reference import indicator_ranges, read_csv

VALUE_FIELD = {"who": "NumericValue", "worldbank": "value", "unicef": "OBS_VALUE"}
COUNTRY_FIELD = {"who": "SpatialDim", "worldbank": "countryiso3code", "unicef": "REF_AREA"}
PERIOD_FIELD = {"who": "TimeDim", "worldbank": "date", "unicef": "TIME_PERIOD"}

# Outcomes that count as correct handling, per fault type (see evaluate.classify for labels).
EXPECTED: dict[str, set[str]] = {
    "missing_value": {"rejected:no_value"},
    "unparseable_value": {"rejected:unparseable_value"},
    "out_of_range": {"rejected:out_of_range"},
    "duplicate": {"rejected:exact_duplicate"},
    "country_recoverable": {"recovered"},
    "country_invalid": {"rejected:unknown_country"},
    "date_noncanonical": {"recovered", "flagged:noncanonical_period"},
    "date_unparseable": {"rejected:invalid_year"},
    "categorical_invalid": {"rejected:unmapped_vocabulary"},
    "value_change": {"flagged:temporal_outlier", "flagged:within_group_disagreement",
                     "flagged:conflict"},
    "schema_rename_required": {"schema:error"},
    "schema_drop_required": {"schema:error"},
    "schema_add_unexpected": {"schema:warned"},
}
VALIDITY_FAULTS = ("missing_value", "unparseable_value", "out_of_range", "duplicate",
                   "country_recoverable", "country_invalid", "date_noncanonical",
                   "date_unparseable", "categorical_invalid")
MAGNITUDES = (0.02, 0.05, 0.10, 0.25, 0.50, 1.0, 3.0, 9.0)  # relative change of the value
SCHEMA_FAULTS = ("schema_rename_required", "schema_drop_required", "schema_add_unexpected")


@dataclass
class Fault:
    fault_type: str
    variant: str
    source: str
    file: str
    row_num: int                 # 1-based position in the corrupted file; 0 for file-level faults
    concept: str = ""
    iso3: str = ""
    year: int = 0
    original: str = ""
    corrupted: str = ""
    origin_row_num: int | None = None     # for duplicates: the row that was copied
    orig_value: float | None = None       # the clean row's value, to tell recovery from silent error
    cell_members: int = 0                 # rows from the same evidence group in the cell
    n_neighbors: int = 0                  # neighbouring observations available to the outlier check
    expected: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class PoolRow:
    source: str
    file: str
    row_num: int
    concept: str
    iso3: str
    year: int
    value: float
    flags: tuple[str, ...]
    cell_flagged: bool
    n_neighbors: int
    group: str
    cell_members: int
    series: tuple


# --- loading the clean snapshot ---------------------------------------------------------

class RawStore:
    """The parsed raw files of the clean snapshot; ``working_copy`` gives a mutable duplicate."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self.manifest = json.loads((self.run_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
        self.entries = {e["path"]: e for e in self.manifest["files"]}
        self.data: dict[str, object] = {}
        for path, entry in self.entries.items():
            raw = (self.run_dir / path).read_bytes()
            if entry["source"] == "unicef":
                reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
                self.data[path] = {"header": list(reader.fieldnames), "rows": list(reader)}
            else:
                self.data[path] = json.loads(raw)

    def working_copy(self) -> dict[str, object]:
        return copy.deepcopy(self.data)


def rows_of(source: str, parsed) -> list[dict]:
    if source == "who":
        return parsed["value"]
    if source == "worldbank":
        return parsed[1]
    return parsed["rows"]


def serialize(source: str, parsed) -> bytes:
    if source != "unicef":
        return json.dumps(parsed, ensure_ascii=False).encode("utf-8")
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=parsed["header"], lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(parsed["rows"])
    return out.getvalue().encode("utf-8")


def write_snapshot(store: RawStore, parsed: dict, run_id: str, out_root: Path) -> Path:
    writer = SnapshotWriter(out_root, scope=store.manifest["scope"], run_id=run_id)
    pages = []
    for path, entry in store.entries.items():
        pages.append(RawPage(entry["source"], entry["series"], entry["concept"], entry["page"],
                             entry["url"], entry["http_status"], entry["content_type"],
                             serialize(entry["source"], parsed[path]), path.rsplit(".", 1)[1]))
    writer.write_pages(pages)
    writer.finalize()
    return writer.run_dir


# --- the pool of injectable rows --------------------------------------------------------

def build_pool(conn: psycopg.Connection, clean_run_id: str) -> list[PoolRow]:
    """Selected headline rows of the clean run, with the context needed to judge detectability."""
    cell_flagged = {(c, i.strip(), y) for c, i, y in conn.execute(
        "SELECT di.concept, c.iso3, r.year FROM core.fact_reconciled r"
        " JOIN core.dim_indicator di USING (indicator_key) JOIN core.dim_country c USING (country_key)"
        " WHERE r.run_id = %s AND (r.conflict OR r.within_group_disagreement)", (clean_run_id,)).fetchall()}
    groups = {(c, s): label for c, s, label in conn.execute(
        "SELECT concept, source_code, group_label FROM core.evidence_group WHERE run_id = %s",
        (clean_run_id,)).fetchall()}
    rows = conn.execute(
        "SELECT s.source_code, f.staging_source_file, f.staging_row_num, i.concept, c.iso3, f.year,"
        " f.value, f.quality_flags, f.source_key, f.country_key, f.indicator_key"
        " FROM core.fact_observation f JOIN core.dim_source s USING (source_key)"
        " JOIN core.dim_indicator i USING (indicator_key) JOIN core.dim_country c USING (country_key)"
        " WHERE f.run_id = %s AND f.is_headline AND f.is_selected"
        " ORDER BY f.source_key, f.country_key, f.indicator_key, f.year", (clean_run_id,)).fetchall()

    pool: list[PoolRow] = []
    for series_key, grp in groupby(rows, key=lambda r: (r[8], r[9], r[10])):
        series = list(grp)
        for idx, r in enumerate(series):
            n_neighbors = len(series[max(0, idx - 2):idx]) + len(series[idx + 1:idx + 3])
            iso3 = r[4].strip()
            pool.append(PoolRow(r[0], r[1], r[2], r[3], iso3, r[5], r[6], tuple(r[7]),
                                (r[3], iso3, r[5]) in cell_flagged, n_neighbors,
                                groups.get((r[3], r[0]), r[0]), 0, series_key))
    members: dict[tuple, int] = {}
    for p in pool:
        members[(p.concept, p.iso3, p.year, p.group)] = members.get((p.concept, p.iso3, p.year, p.group), 0) + 1
    for p in pool:
        p.cell_members = members[(p.concept, p.iso3, p.year, p.group)]
    return pool


def clean_universe(pool: list[PoolRow]) -> list[PoolRow]:
    """Rows eligible for injection: unflagged, in cells with no pre-existing conflict."""
    return [p for p in pool if not p.flags and not p.cell_flagged]


# --- field-level mutations --------------------------------------------------------------

def _scale_bounds(source: str, row: dict, factor: float) -> None:
    keys = {"who": ("Low", "High"), "unicef": ("LOWER_BOUND", "UPPER_BOUND")}.get(source, ())
    for key in keys:
        value = row.get(key)
        try:
            if value not in (None, ""):
                row[key] = (float(value) * factor) if source == "who" else repr(float(value) * factor)
        except ValueError:
            pass


def set_value(source: str, row: dict, new: float, old: float) -> None:
    if source == "who":
        row["NumericValue"], row["Value"] = new, str(new)
    elif source == "worldbank":
        row["value"] = new
    else:
        row["OBS_VALUE"] = repr(new)
    if old:
        _scale_bounds(source, row, new / old)


def _get_value(source: str, row: dict) -> str:
    return str(row.get(VALUE_FIELD[source]))


def _text_period(source: str, row: dict) -> str:
    return str(row.get(PERIOD_FIELD[source]))


def changed_value(rng: random.Random, value: float, magnitude: float, lo: float, hi: float) -> float | None:
    """A value changed by ``magnitude`` (relative), kept inside the plausible range."""
    up, down = value * (1 + magnitude), value / (1 + magnitude) if magnitude >= 1 else value * (1 - magnitude)
    options = [up, down]
    rng.shuffle(options)
    for candidate in options:
        if lo <= candidate <= hi and candidate != value:
            return round(candidate, 6)
    return None


def out_of_range_value(rng: random.Random, value: float, lo: float, hi: float) -> float:
    if rng.random() < 0.5:
        return round(hi * (1 + rng.uniform(0.1, 2.0)), 4)
    return round(-(abs(value) + rng.uniform(1, 100)), 4)


# --- pass builders ----------------------------------------------------------------------

def _country_lookup() -> tuple[dict, dict]:
    countries = {r["iso3"]: r for r in read_csv("countries.csv")}
    aliases: dict[str, list[str]] = {}
    for r in read_csv("country_aliases.csv"):
        aliases.setdefault(r["iso3"], []).append(r["alias"])
    return countries, aliases


def inject_validity(store: RawStore, parsed: dict, pool: list[PoolRow], rng: random.Random,
                    n: int, exclude: set[tuple[str, int]] | None = None,
                    fault_types: tuple[str, ...] = VALIDITY_FAULTS) -> list[Fault]:
    """Pass 1: structural and value-validity faults, ``n`` rows per type, disjoint rows.

    ``exclude`` lists (file, row) identities that must not be touched, so that passes can be combined.
    """
    ranges = indicator_ranges()
    countries, aliases = _country_lookup()
    eligible = clean_universe(pool)
    rng.shuffle(eligible)
    used: set[tuple[str, int]] = set(exclude or ())
    faults: list[Fault] = []
    appended: dict[str, int] = {}

    def take(predicate) -> list[PoolRow]:
        chosen = []
        for p in eligible:
            if (p.file, p.row_num) not in used and predicate(p):
                chosen.append(p)
                used.add((p.file, p.row_num))
                if len(chosen) == n:
                    break
        return chosen

    def row_of(p: PoolRow) -> dict:
        return rows_of(p.source, parsed[p.file])[p.row_num - 1]

    def has_dimension(p: PoolRow) -> bool:
        row = row_of(p)
        if p.source == "who":
            return row.get("Dim1") is not None or row.get("Dim3") is not None
        if p.source == "unicef":
            return any(row.get(c) for c in ("SEX", "WEALTH_QUINTILE", "RESIDENCE"))
        return False

    def base(p: PoolRow, fault_type: str, variant: str, original: str, corrupted: str, row_num=None) -> Fault:
        return Fault(fault_type, variant, p.source, p.file, row_num or p.row_num, p.concept, p.iso3,
                     p.year, original, corrupted, expected=sorted(EXPECTED[fault_type]),
                     cell_members=p.cell_members, n_neighbors=p.n_neighbors, orig_value=p.value)

    for fault_type in fault_types:
        pred = has_dimension if fault_type == "categorical_invalid" else (lambda _p: True)
        for p in take(pred):
            row = row_of(p)
            lo, hi = ranges[p.concept]
            original = _get_value(p.source, row)
            if fault_type == "missing_value":
                if p.source == "who":
                    row["NumericValue"], row["Value"] = None, None
                elif p.source == "worldbank":
                    row["value"] = None
                else:
                    row["OBS_VALUE"] = ""
                faults.append(base(p, fault_type, "blank", original, "<blank>"))
            elif fault_type == "unparseable_value":
                text = rng.choice(["n/a", "NA", "-", "unknown"])
                if p.source == "who":
                    row["NumericValue"], row["Value"] = None, text
                elif p.source == "worldbank":
                    row["value"] = text
                else:
                    row["OBS_VALUE"] = text
                f = base(p, fault_type, text, original, text)
                if p.source == "worldbank":   # staging keeps no raw text for World Bank values
                    f.expected = ["rejected:no_value"]
                faults.append(f)
            elif fault_type == "out_of_range":
                new = out_of_range_value(rng, p.value, lo, hi)
                set_value(p.source, row, new, p.value)
                faults.append(base(p, fault_type, "above_max" if new > hi else "negative", original, str(new)))
            elif fault_type == "duplicate":
                copy_row = copy.deepcopy(row)
                rows = rows_of(p.source, parsed[p.file])
                rows.append(copy_row)
                appended[p.file] = appended.get(p.file, 0) + 1
                f = base(p, fault_type, "exact_copy", original, original, row_num=len(rows))
                f.origin_row_num = p.row_num
                faults.append(f)
            elif fault_type == "country_recoverable":
                ref = countries[p.iso3]
                options = {"lowercase": p.iso3.lower(), "padded": f" {p.iso3} ", "iso2": ref["iso2"],
                           "name": ref["name"]}
                if aliases.get(p.iso3):
                    options["alias"] = rng.choice(aliases[p.iso3])
                variant = rng.choice(sorted(options))
                row[COUNTRY_FIELD[p.source]] = options[variant]
                faults.append(base(p, fault_type, variant, p.iso3, options[variant]))
            elif fault_type == "country_invalid":
                variant = rng.choice(["XXX", "ZZZ", "AFR", "SSA", "ABC"])
                row[COUNTRY_FIELD[p.source]] = variant
                faults.append(base(p, fault_type, variant, p.iso3, variant))
            elif fault_type == "date_noncanonical":
                new = f"{p.year}-{rng.randint(1, 12):02d}" if p.source == "unicef" else f"{p.year}.0"
                row[PERIOD_FIELD[p.source]] = new
                faults.append(base(p, fault_type, "month_suffix" if p.source == "unicef" else "decimal",
                                   str(p.year), new))
            elif fault_type == "date_unparseable":
                options = {"two_digit_year": str(p.year)[2:], "fy_prefix": f"FY{p.year}",
                           "not_available": "N/A", "month_name": f"June {p.year}"}
                variant = rng.choice(sorted(options))
                row[PERIOD_FIELD[p.source]] = options[variant]
                faults.append(base(p, fault_type, variant, str(p.year), options[variant]))
            elif fault_type == "categorical_invalid":
                if p.source == "who":
                    field_name = "Dim1" if row.get("Dim1") is not None else "Dim3"
                    bad = rng.choice(["Both sexes", "B", "total", "ALL"]) if field_name == "Dim1" else "WEALTHQUINTILE_ALL"
                else:
                    field_name = rng.choice([c for c in ("SEX", "WEALTH_QUINTILE", "RESIDENCE") if row.get(c)])
                    bad = rng.choice(["Total", "T", "ALL", "99"])
                faults.append(base(p, fault_type, field_name, str(row[field_name]), bad))
                row[field_name] = bad
    return faults


def inject_magnitude(store: RawStore, parsed: dict, pool: list[PoolRow], rng: random.Random,
                     n: int, magnitudes: tuple[float, ...] = MAGNITUDES, isolate: bool = True,
                     exclude: set[tuple[str, int]] | None = None) -> list[Fault]:
    """Pass 2: change one value by a known relative amount.

    With ``isolate`` (the detection experiment) at most one row per series and per cell is changed, so one
    error cannot contaminate the context used to judge another. Without it (the ablation) corruption is
    random, as real errors would be; a row is still changed at most once.
    """
    ranges = indicator_ranges()
    skip = set(exclude or ())
    eligible = [p for p in clean_universe(pool)
                if (p.n_neighbors >= 3 or p.cell_members >= 2) and (p.file, p.row_num) not in skip]
    rng.shuffle(eligible)
    used_series: set[tuple] = set()
    used_cells: set[tuple] = set()
    used_rows: set[tuple[str, int]] = set()
    faults: list[Fault] = []
    for magnitude in magnitudes:
        count = 0
        for p in eligible:
            cell = (p.concept, p.iso3, p.year)
            if (p.file, p.row_num) in used_rows:
                continue
            if isolate and (p.series in used_series or cell in used_cells):
                continue
            lo, hi = ranges[p.concept]
            new = changed_value(rng, p.value, magnitude, lo, hi)
            if new is None:
                continue
            row = rows_of(p.source, parsed[p.file])[p.row_num - 1]
            original = _get_value(p.source, row)
            set_value(p.source, row, new, p.value)
            used_series.add(p.series)
            used_cells.add(cell)
            used_rows.add((p.file, p.row_num))
            faults.append(Fault("value_change", f"{magnitude:+.0%}".replace("+", ""), p.source, p.file,
                                p.row_num, p.concept, p.iso3, p.year, original, str(new),
                                expected=sorted(EXPECTED["value_change"]), cell_members=p.cell_members,
                                n_neighbors=p.n_neighbors, orig_value=p.value))
            count += 1
            if count == n:
                break
    return faults


ABLATION_MAGNITUDES = (0.10, 0.25, 0.50, 1.0, 3.0)


def inject_mixed(store: RawStore, parsed: dict, pool: list[PoolRow], rng: random.Random,
                 rate: float) -> list[Fault]:
    """Corrupt ``rate`` of the pool's analysis rows, in equal shares across the fault groups.

    The groups are the nine validity fault types and value changes at five magnitudes. Value changes
    are placed first and their rows excluded from the validity faults, so no row is hit twice.
    """
    groups = len(VALIDITY_FAULTS) + len(ABLATION_MAGNITUDES)
    per_group = max(1, round(rate * len(clean_universe(pool)) / groups))
    changes = inject_magnitude(store, parsed, pool, rng, per_group, ABLATION_MAGNITUDES, isolate=False)
    touched = {(f.file, f.row_num) for f in changes}
    validity = inject_validity(store, parsed, pool, rng, per_group, exclude=touched)
    return changes + validity


def inject_schema(store: RawStore, parsed: dict, rng: random.Random, files_per_type: int = 2) -> list[Fault]:
    """Pass 3: file-level schema faults on distinct files."""
    required = {"who": "NumericValue", "worldbank": "countryiso3code", "unicef": "OBS_VALUE"}
    droppable = {"who": "SpatialDim", "worldbank": "date", "unicef": "TIME_PERIOD"}
    paths = sorted(store.entries)
    if len(paths) < len(SCHEMA_FAULTS):
        raise ValueError(f"need at least {len(SCHEMA_FAULTS)} files for the schema pass, have {len(paths)}")
    files_per_type = min(files_per_type, len(paths) // len(SCHEMA_FAULTS))  # distinct files per fault
    rng.shuffle(paths)
    faults: list[Fault] = []
    cursor = 0
    for fault_type in SCHEMA_FAULTS:
        for _ in range(files_per_type):
            path = paths[cursor]
            cursor += 1
            source = store.entries[path]["source"]
            rows = rows_of(source, parsed[path])
            if fault_type == "schema_rename_required":
                old, new = required[source], required[source] + "_x"
                for row in rows:
                    if old in row:
                        row[new] = row.pop(old)
                if source == "unicef":
                    parsed[path]["header"] = [new if c == old else c for c in parsed[path]["header"]]
                detail = f"{old}->{new}"
            elif fault_type == "schema_drop_required":
                old = droppable[source]
                for row in rows:
                    row.pop(old, None)
                if source == "unicef":
                    parsed[path]["header"] = [c for c in parsed[path]["header"] if c != old]
                detail = old
            else:
                new = "NEW_FIELD"
                for row in rows:
                    row[new] = "1"
                if source == "unicef":
                    parsed[path]["header"] = [*parsed[path]["header"], new]
                detail = new
            faults.append(Fault(fault_type, detail, source, path, 0, expected=sorted(EXPECTED[fault_type])))
    return faults


def write_log(faults: list[Fault], path: Path) -> None:
    path.write_text("\n".join(json.dumps(f.to_json(), ensure_ascii=False) for f in faults) + "\n",
                    encoding="utf-8")
