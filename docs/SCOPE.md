# HealthBridge: Scope and Research Design

_Status: v1.0. Sections 1 to 10 are the plan as written before implementation (v0.1) and are kept so that changes stay traceable; section 11 records what was built, what changed and what was not done. Update via pull request, not silently._

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

`raw` (immutable snapshots, checksums, manifests) → `staging` (typed, source-shaped) → `core` (standardized dimensions; `fact_observation`) → `marts` (analytics) → `ml` (feature tables, planned). Data-quality results are stored in `dq`. As built: raw, staging, core, marts and dq are implemented; the `ml` schema was left empty (see section 11).

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

## 11. What was built and what changed (v1.0)

The full account is the [research report](REPORT.md) and the [architecture document](architecture.md). This section lists the differences from the plan above.

**Built as planned.** Three sources, seven concepts, an immutable-snapshot raw layer, staging, core, marts and `dq` in PostgreSQL, a validation suite with a before/after report, a naive-loader comparison, fault injection with precision and false-alarm counts, one ML task with baselines and rolling-origin backtests, a Streamlit dashboard, tests, CI, a Docker image, a README and a short report.

**Changed from the plan.**

| Plan | What happened | Why |
|---|---|---|
| Evaluation 4: the same model on single-source raw data against integrated data | The ablation compares a naive loader with the full pipeline on data corrupted by the fault-injection harness, both scored against the clean truth | The profiling pass (section 4) found that the sources largely republish one estimate, so integrating them adds little new data for a model. What the pipeline can plausibly change is how much corruption harms the forecasts, which is what the ablation measures (the protocol is in [ml_decision.md](ml_decision.md)). |
| Evaluation 3: rebuild from raw and confirm identical outputs by checksum | Idempotent core, marts and load builds are tested, and the data-quality baseline is tested to reproduce its metrics; there is no end-to-end checksum comparison of all outputs | Not done. Outputs are reproducible by construction and by the tests above, but this was not verified as originally described. |
| ML candidates: forecasting, cross-source anomaly detection, clustering | Forecasting only, decided by a pre-registered gate on the data | The gate and its criteria are in [ml_decision.md](ml_decision.md). The other two candidates were not pursued; ML was meant to stay a downstream demonstration. |
| `ml` schema holding feature tables | The schema exists and is empty | The study reads the marts panel and keeps working data in memory and in result files; persisting feature tables had no consumer. |
| Source dependence was not in the plan | Added: dependence between sources is derived from the data and reconciliation never averages | Found in the profiling pass (section 4): the sources largely republish one upstream estimate, so agreement is not independent confirmation. |
| Check set: duplicates, name variants, unit errors, date formats, outliers, dropped values | Implemented as rule-based checks plus temporal outlier, within-group disagreement and (added later) flat-run flags | Unit-error faults were represented as value changes of known size rather than as a separate fault type. |

**Advanced items.** Not built: a FastAPI service (no consumer), workflow orchestration (runs are started by command), dbt (the transformations are a few dozen SQL statements), freshness and drift monitoring, and additional African sources (AfDB, HDX). Context indicators (health expenditure, workforce) were not added.

**Threats to validity.** Section 9 stands. The additions that results made necessary (final-vintage pseudo out-of-sample forecasts, synthetic faults, inferred source dependence, crisis-year estimates) are in section 7 of the report.
