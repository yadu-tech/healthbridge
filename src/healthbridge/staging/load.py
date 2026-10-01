"""Load a verified raw snapshot into the ``staging`` schema.

Loading is idempotent per snapshot: re-loading a run replaces that run's rows inside one
transaction, so a failure leaves the previous state intact. Different runs coexist, which
lets later steps compare snapshots taken at different times.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import psycopg

from healthbridge.ingest.snapshot import MANIFEST_NAME, verify_snapshot
from healthbridge.staging import parse
from healthbridge.staging.schema import SchemaError, SchemaReport, check_schema

# source -> (table, columns, parser)
TABLES: dict[str, tuple[str, tuple[str, ...], Callable[[bytes], list[dict]]]] = {
    "who": ("staging.who_observation", parse.WHO_COLUMNS, parse.parse_who),
    "worldbank": ("staging.worldbank_observation", parse.WORLDBANK_COLUMNS, parse.parse_worldbank),
    "unicef": ("staging.unicef_observation", parse.UNICEF_COLUMNS, parse.parse_unicef),
}


class SnapshotIntegrityError(RuntimeError):
    pass


def schema_reports(run_dir: Path, manifest: dict) -> dict[str, SchemaReport]:
    """Schema report per raw file (path -> report), before anything is loaded."""
    return {entry["path"]: check_schema(entry["source"], (Path(run_dir) / entry["path"]).read_bytes())
            for entry in manifest["files"]}


def load_snapshot(conn: psycopg.Connection, run_dir: Path) -> dict[str, int]:
    """Verify ``run_dir`` then load it; returns rows loaded per source.

    Refuses a snapshot that fails checksum verification, and one whose files lack required
    fields (SchemaError). Unknown extra columns are recorded in ``staging.load_log`` only.
    """
    run_dir = Path(run_dir)
    problems = verify_snapshot(run_dir)
    if problems:
        raise SnapshotIntegrityError(f"{run_dir} failed verification: {problems[:5]}")
    manifest = json.loads((run_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    run_id = manifest["run_id"]
    counts: dict[str, int] = {}

    reports = schema_reports(run_dir, manifest)
    broken = {path: r.missing for path, r in reports.items() if not r.ok}
    if broken:
        raise SchemaError(f"required fields missing: {broken}")

    with conn.transaction():
        for table, *_ in TABLES.values():
            conn.execute(f"DELETE FROM {table} WHERE run_id = %s", (run_id,))
        conn.execute("DELETE FROM staging.load_log WHERE run_id = %s", (run_id,))

        for entry in manifest["files"]:
            table, columns, parser = TABLES[entry["source"]]
            rows = parser((run_dir / entry["path"]).read_bytes())
            column_list = ", ".join(("run_id", "source_file", "row_num", *columns))
            with conn.cursor() as cur, cur.copy(
                f"COPY {table} ({column_list}) FROM STDIN"
            ) as copy:
                for number, row in enumerate(rows, start=1):
                    copy.write_row([run_id, entry["path"], number, *(row[c] for c in columns)])
            conn.execute(
                "INSERT INTO staging.load_log"
                " (run_id, source, series, concept, source_file, sha256, rows_loaded,"
                " unexpected_columns) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (run_id, entry["source"], entry["series"], entry["concept"], entry["path"],
                 entry["sha256"], len(rows), reports[entry["path"]].unexpected),
            )
            counts[entry["source"]] = counts.get(entry["source"], 0) + len(rows)
    return counts
