"""HTTP client shared by all source fetchers: retries with backoff, timeouts, identifying UA."""
from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from healthbridge import __version__

TIMEOUT = (10, 180)  # (connect, read) seconds; UNICEF CSV exports can be slow


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers["User-Agent"] = f"healthbridge/{__version__} (research use; public data)"
    return session


def get(session: requests.Session, url: str, params: dict | None = None) -> requests.Response:
    response = session.get(url, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    return response
