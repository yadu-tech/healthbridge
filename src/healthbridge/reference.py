"""Version-controlled reference data (``reference/*.csv``) and its loading into the core dimensions.

Reference data is small, human-reviewed and changes by commit, so every build is reproducible
from the repository alone. Cross-checks against the staged data live in ``core.build``.
"""
from __future__ import annotations

import csv
import re
import unicodedata

import psycopg

from healthbridge.config import CONCEPTS, UNICEF_BASE, WHO_BASE, WORLDBANK_BASE
from healthbridge.db import REPO_ROOT

REFERENCE_DIR = REPO_ROOT / "reference"

# Fixed tie-break order for reconciliation. Arbitrary but explicit; where sources are
# dependent (same underlying estimate) the choice does not change the value.
SOURCES: tuple[dict, ...] = (
    {"source_key": 1, "source_code": "who", "name": "WHO Global Health Observatory",
     "publisher": "World Health Organization", "base_url": WHO_BASE, "priority": 1},
    {"source_key": 2, "source_code": "unicef", "name": "UNICEF Data Warehouse",
     "publisher": "UNICEF", "base_url": UNICEF_BASE, "priority": 2},
    {"source_key": 3, "source_code": "worldbank", "name": "World Bank Open Data",
     "publisher": "World Bank", "base_url": WORLDBANK_BASE, "priority": 3},
)


class ReferenceMismatch(RuntimeError):
    """Reference data and the staged data disagree (e.g. an ISO2 code)."""


def read_csv(name: str) -> list[dict[str, str]]:
    with (REFERENCE_DIR / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def normalize_alias(text: str) -> str:
    """Lower-case, strip accents and anything that is not a letter or digit."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", folded.lower())


def indicator_ranges() -> dict[str, tuple[float, float]]:
    return {r["concept"]: (float(r["valid_min"]), float(r["valid_max"]))
            for r in read_csv("indicators.csv")}


def load_reference(conn: psycopg.Connection, years: tuple[int, int]) -> None:
    """Upsert dimensions and crosswalks from reference/ (idempotent)."""
    for s in SOURCES:
        conn.execute(
            "INSERT INTO core.dim_source (source_key, source_code, name, publisher, base_url, priority)"
            " VALUES (%(source_key)s, %(source_code)s, %(name)s, %(publisher)s, %(base_url)s, %(priority)s)"
            " ON CONFLICT (source_key) DO UPDATE SET name = EXCLUDED.name,"
            " publisher = EXCLUDED.publisher, base_url = EXCLUDED.base_url, priority = EXCLUDED.priority",
            s,
        )
    for r in read_csv("countries.csv"):
        conn.execute(
            "INSERT INTO core.dim_country (iso3, iso2, name, un_subregion)"
            " VALUES (%(iso3)s, %(iso2)s, %(name)s, %(un_subregion)s)"
            " ON CONFLICT (iso3) DO UPDATE SET iso2 = EXCLUDED.iso2, name = EXCLUDED.name,"
            " un_subregion = EXCLUDED.un_subregion",
            r,
        )
    for r in read_csv("indicators.csv"):
        conn.execute(
            "INSERT INTO core.dim_indicator (concept, name, unit, domain, valid_min, valid_max,"
            " unicef_headline_age, definition) VALUES (%(concept)s, %(name)s, %(unit)s, %(domain)s,"
            " %(valid_min)s, %(valid_max)s, %(age)s, %(definition)s)"
            " ON CONFLICT (concept) DO UPDATE SET name = EXCLUDED.name, unit = EXCLUDED.unit,"
            " domain = EXCLUDED.domain, valid_min = EXCLUDED.valid_min, valid_max = EXCLUDED.valid_max,"
            " unicef_headline_age = EXCLUDED.unicef_headline_age, definition = EXCLUDED.definition",
            {**r, "age": r["unicef_headline_age"] or None},
        )
    for c in CONCEPTS:
        for source, code in (("who", c.who_code), ("worldbank", c.worldbank_code),
                             ("unicef", f"{c.unicef_flow}.{c.unicef_indicator}")):
            conn.execute(
                "INSERT INTO core.indicator_source_map (concept, source_code, source_series_code)"
                " VALUES (%s, %s, %s) ON CONFLICT (concept, source_code)"
                " DO UPDATE SET source_series_code = EXCLUDED.source_series_code",
                (c.name, source, code),
            )
    for r in read_csv("vocabulary.csv"):
        conn.execute(
            "INSERT INTO core.map_vocab (dimension, source_code, source_value, canonical, note)"
            " VALUES (%(dimension)s, %(source)s, %(source_value)s, %(canonical)s, %(note)s)"
            " ON CONFLICT (dimension, source_code, source_value)"
            " DO UPDATE SET canonical = EXCLUDED.canonical, note = EXCLUDED.note",
            {**r, "note": r["note"] or None},
        )
    for year in range(years[0], years[1] + 1):
        conn.execute("INSERT INTO core.dim_year (year) VALUES (%s) ON CONFLICT DO NOTHING", (year,))
    _load_aliases(conn)


def _load_aliases(conn: psycopg.Connection) -> None:
    for iso3, name in conn.execute("SELECT iso3, name FROM core.dim_country").fetchall():
        _add_alias(conn, name, iso3, "canonical_name")
    for r in read_csv("country_aliases.csv"):
        _add_alias(conn, r["alias"], r["iso3"], r["alias_type"])


def _add_alias(conn: psycopg.Connection, alias: str, iso3: str, alias_type: str) -> None:
    norm = normalize_alias(alias)
    existing = conn.execute(
        "SELECT iso3 FROM core.country_alias WHERE alias_norm = %s", (norm,)
    ).fetchone()
    if existing and existing[0].strip() != iso3:
        raise ReferenceMismatch(f"alias {alias!r} already maps to {existing[0]}, not {iso3}")
    conn.execute(
        "INSERT INTO core.country_alias (alias_norm, alias, iso3, alias_type) VALUES (%s, %s, %s, %s)"
        " ON CONFLICT (alias_norm) DO NOTHING",
        (norm, alias, iso3, alias_type),
    )
