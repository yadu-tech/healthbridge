"""Orchestrates one ingestion run across sources and concepts."""
from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from pathlib import Path

import requests

from healthbridge.config import (
    AFRICA_ISO3,
    CONCEPTS,
    SOURCE_PAUSE_SECONDS,
    Concept,
    default_years,
)
from healthbridge.ingest.http import make_session
from healthbridge.ingest.snapshot import SnapshotWriter
from healthbridge.ingest.sources import SOURCES

log = logging.getLogger(__name__)


def run_ingestion(
    out_dir: Path,
    sources: Iterable[str] = tuple(SOURCES),
    concepts: Iterable[Concept] = CONCEPTS,
    years: tuple[int, int] | None = None,
    session: requests.Session | None = None,
    run_id: str | None = None,
    pause_seconds: float | None = None,
) -> SnapshotWriter:
    """Fetch every (source, concept) pair into a new snapshot and write its manifest.

    A series is written only if it was fetched completely, so a snapshot never holds a
    half-downloaded series. Failures are recorded in the manifest and the run continues.
    ``pause_seconds`` overrides the per-source politeness delay (tests pass 0).
    """
    sources, concepts = list(sources), list(concepts)
    unknown = [s for s in sources if s not in SOURCES]
    if unknown:
        raise ValueError(f"unknown source(s): {unknown}; choose from {sorted(SOURCES)}")
    years = years or default_years()
    session = session or make_session()
    writer = SnapshotWriter(
        out_dir,
        scope={
            "sources": sources,
            "concepts": [c.name for c in concepts],
            "countries": list(AFRICA_ISO3),
            "years": list(years),
        },
        run_id=run_id,
    )
    for source in sources:
        pause = SOURCE_PAUSE_SECONDS.get(source, 0.0) if pause_seconds is None else pause_seconds
        for index, concept in enumerate(concepts):
            if index and pause:
                time.sleep(pause)
            try:
                pages = list(SOURCES[source](session, concept, years))
            except Exception as exc:  # noqa: BLE001 - record any failure, keep the run going
                log.error("%s / %s failed: %s", source, concept.name, exc)
                writer.add_failure(source, concept.name, exc)
                continue
            writer.write_pages(pages)
            log.info("%s / %s: %d page(s)", source, concept.name, len(pages))
    writer.finalize()
    return writer
