# HealthBridge

**Bridging disconnected African healthcare datasets.**
A reproducible data-engineering platform that ingests public maternal and child health data from multiple sources, validates and harmonizes it, and serves reliable data for analytics and downstream machine learning.

> **Status: early development.** Implemented so far: raw ingestion, staging, data-quality measurement and the core (integrated) layer. Analytics, machine learning, the API and the dashboard are planned. Sections marked _planned_ describe intended work.

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

## Data-quality baseline

Measures the staged (not yet cleaned) data and stores every metric in `dq.metric`, so the "before" state is reproducible and queryable:

```bash
python -m healthbridge.dq baseline            # measure the latest loaded snapshot
python -m healthbridge.dq report --out docs/results/dq_baseline.md
```

Dimensions: **completeness** (row-level missing rate and country-year grid coverage), **validity** (unparseable values, plausible ranges, values outside the source's own uncertainty bounds, invalid years or country codes), **uniqueness** (exact duplicates, and rows ambiguous at the (country, year) grain), **consistency** (non-year period formats) and **integration** (schema and vocabulary heterogeneity, naive cross-source join fan-out). Scores are reported per dimension; the composite is an unweighted mean with a leave-one-dimension-out range because the weights are arbitrary.

First baseline (snapshot `20261001T110703Z`, full report in [docs/results/dq_baseline.md](docs/results/dq_baseline.md)):

- Values are almost entirely **valid** (no range, bounds, year or country-code violations), but **uniqueness at the analysis grain is low** (UNICEF 14%, WHO 27%): the sources publish sex, wealth-quintile and survey breakdowns alongside national totals.
- A naive (country, year) join across the three sources multiplies rows: **58x** for under-5 mortality and **228x** for stunting.
- 7 of the 54 African states (Djibouti, Egypt, Libya, Morocco, Sudan, Somalia, Tunisia) are outside WHO's `AFR` region.
- WHO and UNICEF code the same categories differently (e.g. `SEX_BTSX` vs `_T`), and UNICEF's seven series use four different column sets.

The checks are tested against a hand-built snapshot with a known number of seeded defects, and the baseline is reproducible: re-running it gives identical metrics.

## Core layer (integration)

Turns the three source-shaped staging tables into one consistent model:

```bash
python -m healthbridge.core build                 # staging -> core (dimensions, fact, reconciliation)
python -m healthbridge.dq baseline --stage core   # measure the core layer with the same engine
python -m healthbridge.dq compare --out docs/results/before_after.md
```

- **Explicit grain.** Every row keeps canonical sex, wealth, residence, maternal-education and age dimensions; nothing is deleted to force uniqueness. The default analyst view, `core.v_headline_observation`, has one national-total row per source, country, indicator and year.
- **Documented rules.** Headline definitions, the vocabulary crosswalk, survey selection, rejection reasons and reconciliation are recorded with their evidence in [docs/harmonization.md](docs/harmonization.md). Reference data (countries, indicators, vocabulary) is version-controlled in `reference/`.
- **Source dependence is derived, not assumed.** Sources that publish the same underlying estimate form one evidence group, so agreement between them is not counted as independent confirmation. Reconciled values are never averages, and conflicts between independent groups are flagged.
- **Lineage.** Every core row points back to its staging row and raw snapshot file; every rejected row is stored with a reason.

First before/after comparison (full report: [docs/results/before_after.md](docs/results/before_after.md)):

| | Before (staging) | After (core) |
|---|---|---|
| Rows unique at the (country, indicator, year) grain | 30.5% | 100% (by construction) |
| Naive 3-source join, under-5 mortality / stunting | 58x / 228x row inflation | 1.0x |
| "Conflicting" joined stunting rows | 82% (mostly breakdowns vs totals) | 16% (genuine disagreement) |
| Concepts with independent cross-validation | not measurable | 1 of 7 (stunting) |
| Records rejected | not tracked | 3,478 of 90,503, all empty World Bank placeholders |

For six of the seven concepts, WHO, UNICEF and the World Bank publish the same underlying estimate (at least 93% of shared country-years agree within 1%), so their agreement cannot validate the number. Stunting is the exception: WHO's model-based estimates and the survey-based UNICEF and World Bank values disagree by more than 10% in 15.6% of the 326 country-years they share.

## Roadmap

See [docs/SCOPE.md](docs/SCOPE.md) for MVP vs advanced features, evaluation design and threats to validity.

## Limitations and ethics

Documented in [docs/SCOPE.md](docs/SCOPE.md) and expanded as results are produced.

## License

MIT, see [LICENSE](LICENSE).
