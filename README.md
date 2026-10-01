# HealthBridge

**Bridging disconnected African healthcare datasets.**
A reproducible data-engineering platform that ingests public maternal and child health data from multiple sources, validates and harmonizes it, and serves reliable data for analytics and downstream machine learning.

> **Status: early development.** The repository currently contains the project scope, a source-profiling script, local infrastructure and the raw ingestion layer. Sections marked _planned_ describe intended work.

## Problem

Public health data about Africa is spread across organizations that publish it with different schemas, code systems, granularities and definitions. Reconciling these sources is slow, and unreconciled data can quietly weaken analytics and models. See [docs/SCOPE.md](docs/SCOPE.md).

## Research question

How can automated data integration and data-quality validation improve the reliability and usability of heterogeneous healthcare data for analytics and AI applications in Africa?

## Data sources

| Source | Access |
|---|---|
| WHO Global Health Observatory | OData API, no registration |
| World Bank indicators | REST API, no registration |
| UNICEF data warehouse | SDMX API, no registration |

Only aggregate, public, non-personal data is used.

## Architecture _(planned)_

`raw` → `staging` → `core` (star schema) → `marts` → `ml`, with data-quality results in `dq`. Details in [docs/SCOPE.md](docs/SCOPE.md).

## Getting started

Requirements: Python 3.11+, Docker Desktop.

```bash
cp .env.example .env          # set a local password
docker compose up -d          # start PostgreSQL
python -m venv .venv && .venv/Scripts/activate
pip install -e ".[dev]"
python -m healthbridge.staging migrate   # create the layered schemas and staging tables
pytest
python scripts/profile_sources.py   # regenerate docs/source_profile.md
```

## Raw ingestion layer

Fetches the seven maternal and child health concepts for the 54 African states from WHO, the World Bank and UNICEF into an **immutable, checksummed snapshot**:

```bash
python -m healthbridge.ingest run                 # new snapshot under data/raw/<UTC timestamp>/
python -m healthbridge.ingest verify data/raw/<run_id>   # recompute every SHA-256 against the manifest
```

- Responses are stored byte-for-byte; nothing is cleaned or reshaped in the raw layer.
- `manifest.json` records, for every file, its source URL, HTTP status, size and SHA-256, plus the run scope, package and Python versions, and any failures.
- A series is written only if fully downloaded. A failed series is recorded in the manifest and the run continues; the command exits non-zero.
- Requests are paced per source and retried with backoff (UNICEF returns HTTP 429 on bursts).
- Snapshots are never overwritten, so any analysis can name the exact data it used.

Design notes from live testing: the WHO GHO API rejects queries with more than 100 filter nodes, so the 54-country filter uses OData `in (...)`. The World Bank reports some errors with HTTP 200, so payloads are checked, not just status codes.

## Staging layer

Loads a verified raw snapshot into typed, **source-shaped** PostgreSQL tables (`staging.who_observation`, `staging.worldbank_observation`, `staging.unicef_observation`):

```bash
python -m healthbridge.staging migrate        # apply versioned SQL migrations (sql/migrations)
python -m healthbridge.staging load [snapshot_dir]   # default: latest snapshot in data/raw
```

- Staging **types** values but does not clean, deduplicate or harmonize them; that happens in later layers.
- Every row records its lineage (`run_id`, `source_file`, `row_num`), and `staging.load_log` stores each file's SHA-256 and row count.
- Unparseable values become NULL while the raw text is kept, so the data-quality layer can still count them.
- UNICEF dataflows have different dimension columns, so shared fields are real columns and the rest go to a `jsonb` column.
- A snapshot that fails checksum verification is refused. Loading is idempotent and transactional: a failed load leaves previous data untouched. Several snapshots can coexist.
- Schema changes are versioned migrations with checksums; editing an applied migration is an error.

## Roadmap

See [docs/SCOPE.md](docs/SCOPE.md) for MVP vs advanced features, evaluation design and threats to validity.

## Limitations and ethics

Documented in [docs/SCOPE.md](docs/SCOPE.md) and expanded as results are produced.

## License

MIT, see [LICENSE](LICENSE).
