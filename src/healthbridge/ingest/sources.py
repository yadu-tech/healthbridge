"""Fetchers for WHO GHO, the World Bank and UNICEF.

Each fetcher yields ``RawPage`` objects holding the response bytes exactly as received.
Nothing is parsed or cleaned here; the raw layer must stay faithful to the source.
Parsing is only used to follow pagination and to detect error payloads.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

import requests

from healthbridge.config import (
    AFRICA_ISO3,
    UNICEF_BASE,
    WHO_BASE,
    WORLDBANK_BASE,
    Concept,
)
from healthbridge.ingest.http import get


class SourceError(RuntimeError):
    """The source answered, but not with usable data."""


@dataclass(frozen=True)
class RawPage:
    source: str
    series: str
    concept: str
    page: int
    url: str
    status: int
    content_type: str
    content: bytes
    extension: str


def _page(source: str, series: str, concept: Concept, page: int, resp: requests.Response,
          extension: str) -> RawPage:
    return RawPage(
        source=source,
        series=series,
        concept=concept.name,
        page=page,
        url=resp.url,
        status=resp.status_code,
        content_type=resp.headers.get("Content-Type", ""),
        content=resp.content,
        extension=extension,
    )


def fetch_who(session: requests.Session, concept: Concept, years: tuple[int, int]) -> Iterator[RawPage]:
    """WHO GHO OData: country-level rows for the 54 African states, following nextLink."""
    # `in (...)` rather than a chain of `or`: the GHO API rejects queries over 100 filter nodes.
    countries = ",".join(f"'{iso}'" for iso in AFRICA_ISO3)
    odata_filter = (
        f"SpatialDimType eq 'COUNTRY' and SpatialDim in ({countries}) "
        f"and TimeDim ge {years[0]} and TimeDim le {years[1]}"
    )
    url: str | None = f"{WHO_BASE}/{concept.who_code}"
    params: dict | None = {"$filter": odata_filter}
    page = 0
    while url:
        resp = get(session, url, params)
        payload = resp.json()
        if "value" not in payload:
            raise SourceError(f"WHO {concept.who_code}: response has no 'value' field")
        page += 1
        yield _page("who", concept.who_code, concept, page, resp, "json")
        url, params = payload.get("@odata.nextLink"), None


def fetch_worldbank(session: requests.Session, concept: Concept,
                    years: tuple[int, int]) -> Iterator[RawPage]:
    """World Bank v2: one indicator for all 54 countries, paging until the last page."""
    url = f"{WORLDBANK_BASE}/country/{';'.join(AFRICA_ISO3)}/indicator/{concept.worldbank_code}"
    page, total_pages = 1, 1
    while page <= total_pages:
        params = {"format": "json", "per_page": 20000, "page": page,
                  "date": f"{years[0]}:{years[1]}"}
        resp = get(session, url, params)
        payload = resp.json()
        # The World Bank reports errors (e.g. unknown indicator) with HTTP 200.
        if not isinstance(payload, list) or not payload or "message" in payload[0]:
            raise SourceError(f"World Bank {concept.worldbank_code}: {str(payload)[:200]}")
        total_pages = int(payload[0].get("pages", 1))
        yield _page("worldbank", concept.worldbank_code, concept, page, resp, "json")
        page += 1


def fetch_unicef(session: requests.Session, concept: Concept,
                 years: tuple[int, int]) -> Iterator[RawPage]:
    """UNICEF SDMX: CSV export with code (not label) columns, so country ids are ISO3."""
    key = f"{'+'.join(AFRICA_ISO3)}.{concept.unicef_indicator}"
    url = f"{UNICEF_BASE}/data/UNICEF,{concept.unicef_flow},1.0/{key}"
    params = {"format": "csv", "startPeriod": years[0], "endPeriod": years[1]}
    resp = get(session, url, params)
    if "REF_AREA" not in resp.text.splitlines()[0]:
        raise SourceError(f"UNICEF {concept.unicef_indicator}: unexpected CSV header")
    yield _page("unicef", f"{concept.unicef_flow}.{concept.unicef_indicator}", concept, 1, resp, "csv")


Fetcher = Callable[[requests.Session, Concept, tuple[int, int]], Iterator[RawPage]]

SOURCES: dict[str, Fetcher] = {
    "who": fetch_who,
    "worldbank": fetch_worldbank,
    "unicef": fetch_unicef,
}
