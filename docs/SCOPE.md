# HealthBridge: Scope and Research Design

_Status: v0.1, written before implementation. Update via pull request/commit, not silently, so scope changes remain traceable._

## 1. Problem

Public health data about Africa is published by several organizations (WHO, the World Bank, UNICEF and others) in different structures, code systems, granularities and update cycles. Analysts spend substantial effort reconciling them, and unreconciled data can silently degrade analytics and models.

## 2. Research question

> How can automated data integration and data-quality validation improve the reliability and usability of heterogeneous healthcare data for analytics and AI applications in Africa?

Sub-questions:

- **RQ1 (integration):** How much cross-source inconsistency (identifiers, grain, definitions, values) can automated harmonization resolve, and how much remains irreducible and must be surfaced to the user?
- **RQ2 (validation):** How accurately do automated checks detect data-quality problems? Measured with seeded corruptions of known ground truth (precision and recall per check).
- **RQ3 (downstream):** Does the integrated, validated dataset yield more reproducible and more accurate analytics and models than a naive integration of the same sources?

## 3. Scope

**Domain:** maternal and child health, 54 African states, 1990 onward where available.

**MVP sources** (all public, no registration): WHO Global Health Observatory (OData), World Bank indicators API, UNICEF SDMX data warehouse.

**Initial concepts (7):** under-5 mortality, neonatal mortality, maternal mortality ratio, skilled birth attendance, DTP3 coverage, measles (MCV1) coverage, stunting prevalence. Context indicators (e.g. health expenditure, workforce) may be added after the core pipeline works.

**Out of MVP:** individual-level or survey microdata (e.g. DHS, which requires registration and restricts redistribution), IHME/GBD (licence limits), subnational data, streaming, cloud deployment, Spark (data volume is roughly 10^5 rows; distributed processing is not justified).

## 4. Findings from the source-profiling pass (2026-09-30)

See `docs/source_profile.md` (regenerate with `python scripts/profile_sources.py`).

- The three sources largely republish the same upstream estimates (UN IGME, WUENIC, MMEIG). Agreement between them is not independent confirmation. Provenance must be modelled explicitly.
- Heterogeneity is mostly structural: WHO series carry sex and wealth-quintile dimensions (naively multiplying rows), legacy/duplicate indicator codes exist, and UNICEF may hold several surveys per country-year.
- Coverage differs sharply: the World Bank is about 79% missing for survey-based indicators (skilled birth attendance, stunting).
- Stunting shows genuine cross-source conflict (model-based vs survey-based; median relative difference about 17-19%).

Consequence: the research contribution is **provenance-aware integration and grain/definition harmonization**, evaluated with fault injection because raw source quality is already high.

## 5. Architecture

`raw` (immutable snapshots, checksums, manifests) → `staging` (typed, source-shaped) → `core` (standardized dimensions; `fact_observation`) → `marts` (analytics, planned) → `ml` (feature tables, planned). Data-quality results are stored in `dq`. Raw, staging, core and dq are implemented.

Core model (implemented): `dim_country` (ISO3/ISO2, canonical name, UN M49 sub-region, WHO region; name aliases in `country_alias`), `dim_indicator` (harmonized concept with unit, definition and plausible range; source codes in `indicator_source_map`), `dim_source`, `dim_year` (the data is annual, so a year dimension replaces a full date dimension), and `fact_observation` at an explicit grain (source, country, indicator, year, sex, wealth quintile, residence, maternal education, age group, upstream series) with lineage, quality flags and selection flags. `fact_reconciled` holds one value per country-indicator-year with evidence-group provenance. Rejected rows are stored with reasons in `rejected_record`. Every rule and its evidence is recorded in [harmonization.md](harmonization.md).

## 6. Evaluation design

1. **Naive baseline vs pipeline:** join on country-name strings without validation vs the HealthBridge pipeline. Metrics: join success rate, country-year coverage, conflicts surfaced vs silently overwritten.
2. **Fault injection:** inject known errors (duplicates, name variants, unit errors, date-format mixes, outliers, dropped values) into copies of clean data. Metrics: precision and recall per check.
3. **Reproducibility:** rebuild from raw snapshots and confirm identical outputs (checksums).
4. **Downstream ablation:** train and evaluate the same model on single-source raw vs integrated data.

Data-quality dimensions are reported separately (completeness, validity, uniqueness, consistency, cross-source agreement). Any composite score documents its weights and includes a sensitivity check.

## 7. Machine-learning component

Chosen after profiling, as a downstream demonstration only. Candidates: forecasting of sparse or noisy indicators (immunization coverage, stunting); cross-source anomaly detection; exploratory country clustering. Requirements: naive and statistical baselines, rolling-origin backtests, leave-country-out validation, documented limitations. No causal claims and no country rankings framed as good or bad.

## 8. MVP vs advanced

**MVP:** 3 sources, 7 concepts, raw-to-core pipeline in PostgreSQL, validation suite and DQ report, baseline comparison and fault injection, one ML task with baselines, Streamlit dashboard, tests, CI, README and short report.

**Advanced (if time allows):** FastAPI service, workflow orchestration (Airflow/Dagster/Prefect), dbt, additional African sources (AfDB, HDX), freshness and drift monitoring.

## 9. Threats to validity (initial)

- Many series are modelled estimates, not direct observations.
- Definitions differ across sources; harmonization involves judgement.
- Sparse data in fragile or conflict-affected states.
- Seeded corruptions are a proxy for real-world errors.
- Cross-country correlations are ecological and not causal.
- Estimates are revised between releases; results depend on the snapshot date.

## 10. Ethics

Only aggregate, public, non-personal data. Country-level differences are preserved and cross-country comparison limits are documented. Uncertainty is shown with results.
