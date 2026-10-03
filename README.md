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

## Fault-injection experiment

How well do the pipeline's checks detect known errors? A seeded experiment corrupts a *copy* of the raw snapshot at known positions, runs the real pipeline on it, and records what happened to every injected row:

```bash
python -m healthbridge.experiments run --seeds 5 --resume   # about 25 minutes; resumable
python -m healthbridge.experiments report --out docs/results/fault_injection.md
```

Full report: [docs/results/fault_injection.md](docs/results/fault_injection.md). 11,030 faults across 5 seeds and 15 corrupted snapshots (code version recorded per run; raw results are reproducible from the seeds, not committed).

- **Rule-based checks** (missing, unparseable, out-of-range, duplicate, invalid or recoverable country identifier, bad date, bad vocabulary): all 9,000 injected faults were handled as expected, and 30 of 30 file-level schema faults were caught. This was designed to hold, so it shows the rules work end to end rather than that unanticipated errors would be caught.
- **Value changes of known size** are the informative result. Detection is 0% at 2%, 57% at 5%, 96% at 10%, and at least 99.6% from 25% upward. The 5% row is a threshold effect: increases are caught 16% of the time and decreases 96%, because the disagreement threshold applies to the spread divided by the mean.
- **Each statistical check is reported on the rows it can assess.** The disagreement check caught 100% of changes of 10% or more where another source was available; the outlier check rose from 58% at +25% to 97% at +900% among rows with enough neighbouring observations (92% of rows).
- **False alarms:** on the unmodified data the outlier check flags 0.85% of rows and the disagreement check 1.1% of cells. The outlier check's few false positives in the experiment (precision 92%) were traced to a side effect: rejecting a row removes it from its neighbours' context.
- **Naive loader** (parse, exact ISO3 join, drop nulls): it accepts every out-of-range value, every duplicate and every value change; the pipeline lets 18% of the 2,000 value changes through, all but one at 10% or below.
- **Not covered:** valid-looking corruptions (a swapped but valid country or sex code, a small change with no context and no second source), and errors of kinds not injected here.

## Analytics marts

Analysis-ready tables built from the core layer, for one snapshot at a time:

```bash
python -m healthbridge.marts build                       # panel, latest values, trends, regions, associations, equity gaps, trust
python -m healthbridge.marts report --out docs/results/analytics.md
```

Definitions, grains and caveats: [docs/analytics.md](docs/analytics.md). Results on the real snapshot: [docs/results/analytics.md](docs/results/analytics.md). Every value in the panel carries a quality tier, so an analysis can see which numbers could be cross-checked; associations are cross-country and descriptive only; regional averages are of countries, not people.

What the first report shows (descriptive, one snapshot):

- **Few values can be cross-checked.** Six of seven indicators have a single evidence group; stunting is the exception (19% of values cross-validated, 3.6% in conflict).
- **Countries in a region differ widely.** For example, 2015 under-5 mortality in Western Africa has a median of 92 per 1000 with a range of 19 to 137.
- **Progress since 2000 is broad but uneven.** Under-5 mortality fell in 96% of countries (median -3.5% a year) and maternal mortality in 91% (median -3.0% a year); DTP3 coverage rose in 76%.
- **Associations are strong but are only associations.** Across countries, under-5 mortality has a rank correlation of -0.74 with DTP3 coverage and -0.68 with skilled birth attendance (n = 54 and 49); the change between 2000 and 2015 is correlated at -0.54 with DTP3 change. Countries are not independent observations, so the intervals understate uncertainty.
- **Equity gaps are large and consistent.** Using each country's latest gap, the poorest-to-richest ratio of under-5 mortality is above 1 in all 48 countries with data (median 1.7), girls have lower under-5 mortality than boys in all 54 (median ratio 0.84), and skilled birth attendance is lower among the poorest (median 0.72).
- **Some series are out of date.** Skilled birth attendance is a median of 3 years behind (up to 14); 37% of countries are 5 or more years behind.
- **Some extreme values are unverified.** A few countries show large short-run rises in under-5 mortality (single-year spikes and multi-year shifts). The outlier check flags the former but not the latter, and these values have not been checked against source documentation.

## Dashboard

A Streamlit app over the analytics marts, with six pages: an overview of how far each indicator can be trusted, country profiles, country comparison, relationships between indicators, equity gaps, and the pipeline and data-quality record.

```bash
pip install -e ".[dashboard]"
streamlit run src/healthbridge/dashboard/app.py
```

Trust is part of the interface (quality tiers and flags appear on every series), relationships carry an "associations, not causes" notice, countries are never ranked, and every chart has a table view. The colour palette was checked with a validator for light and dark modes. Design decisions, the validation record and limitations: [docs/dashboard.md](docs/dashboard.md).

## Machine learning: a gate, then models

Machine learning is a downstream demonstration, so whether to do it is itself decided from the data. The criteria and thresholds were written down and committed **before** any backtest was run ([docs/ml_decision.md](docs/ml_decision.md)), and the gate applies them:

```bash
python -m healthbridge.ml gate --out docs/results/ml_gate.md
```

It backtests three simple baselines (last value, linear trend, average change) with rolling origins that cannot see data after their origin, and a bootstrap that resamples countries. First result ([docs/results/ml_gate.md](docs/results/ml_gate.md)):

- **Go for four indicators** (under-5 mortality, maternal mortality, DTP3 and measles coverage); **no-go for three** (neonatal mortality and stunting, whose baselines are already too accurate to leave room, and skilled birth attendance, which is too sparse).
- No single baseline wins: repeating the last value is best for the noisy coverage series, a linear trend for the smoothly falling mortality series.
- The thresholds cut through a continuum (stunting misses by 0.4 percentage points), and the series are final-vintage modelled estimates, so backtests are pseudo out-of-sample. Both caveats are recorded.

**Stage 2** compares a ridge model and gradient-boosted trees, pooled across countries and indicators, with the baselines, using a panel-level rolling origin in which training never sees a year after the cut-off:

```bash
python -m healthbridge.ml models --out docs/results/ml_models.md
```

Results ([docs/results/ml_models.md](docs/results/ml_models.md), outcome and disclosure in [docs/ml_decision.md](docs/ml_decision.md)):

- **Gradient boosting is useful for the two mortality indicators** at 4-5 years (about 3 percentage points lower median error than the best baseline, intervals clear of zero) and beats the baseline at all five cut-offs. It is **not** useful for DTP3 or measles coverage, where repeating the last value is hard to beat.
- The advantage is at long horizons: at one year a simple linear trend is better, at 6-10 years boosting is about 8 points better.
- The **ridge model is not useful for any indicator** and is poor in sample too, so it is a poor fit, not a leak. It was reported as pre-registered, not rescued after the fact.
- These are pseudo out-of-sample results on final-vintage modelled estimates, so real-time accuracy would be lower; they are not predictions for policy.

The data-quality ablation (does corrupted, unvalidated data degrade forecasts, and does the pipeline prevent it?) is specified in the same document and is the next step.

## Roadmap

See [docs/SCOPE.md](docs/SCOPE.md) for MVP vs advanced features, evaluation design and threats to validity.

## Limitations and ethics

Documented in [docs/SCOPE.md](docs/SCOPE.md) and expanded as results are produced.

## License

MIT, see [LICENSE](LICENSE).
