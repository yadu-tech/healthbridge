# HealthBridge: automated integration and validation of public African maternal and child health data

_Research report. Snapshot `20261001T110703Z` (ingested 2026-10-01). Every figure below is taken from a generated report in [results/](results/); the commands that regenerate them are in the [README](../README.md)._

## 1. Summary

**Question.** How can automated data integration and data-quality validation improve the reliability and usability of heterogeneous healthcare data for analytics and AI in Africa?

**What was built.** A reproducible pipeline that ingests WHO, World Bank and UNICEF indicators for 54 African states into immutable, checksummed snapshots; loads them into PostgreSQL; harmonizes them at an explicit grain with row-level lineage; measures data quality before and after with one engine; and serves analysis-ready tables, a dashboard and a forecasting study.

**What was found.**

1. **Integration (RQ1).** Most of the apparent inconsistency across sources is structural, not disagreement about values. A naive country-year join multiplies rows 58-fold for under-5 mortality and 228-fold for stunting; after harmonization the join is 1:1. What remains is real: for six of seven indicators the three sources publish the same upstream estimate, so their agreement is not independent confirmation, and only stunting can be cross-checked (15.6% of 326 shared country-years disagree by more than 10%).
2. **Validation (RQ2).** Rule-based checks handled all 9,000 injected rule violations and 30 of 30 schema faults, which they were designed to do. The informative result is for corrupted values: detection is 0% at a 2% change, 57% at 5%, 96% at 10% and at least 99.6% from 25% upward. Overall 96.6% of 11,000 row-level faults were rejected, flagged or repaired; a naive loader accepted every value change, duplicate and out-of-range value.
3. **Downstream effect (RQ3).** The decision to attempt forecasting was made from the data: it qualifies for four of seven indicators. Gradient boosting beat the best simple baseline for the two mortality indicators (about 3 percentage points lower median error at 4-5 years) but not for the two immunization indicators. In an ablation that corrupted the data, validation protected the forecasts, but modestly: at 10% corruption the naive route lost 0.95 percentage points of accuracy and the pipeline 0.16 (the paired difference of 0.73 has a 95% interval of 0.33 to 1.10), at the price of forecasting fewer series.

**How far this goes.** The results show that explicit grain, provenance-aware reconciliation and layered validation make the data measurably more usable and somewhat more reliable for a forecasting task. They do not show that the pipeline would catch faults of kinds not injected, or that the forecasts are fit for decisions. Section 7 lists what bounds the claims.

## 2. Scope and design

Domain: maternal and child health, 54 African states, 1990 onward. Seven concepts: under-5 mortality, neonatal mortality, maternal mortality ratio, skilled birth attendance, DTP3 coverage, measles (MCV1) coverage and stunting. Only aggregate, public, non-personal data is used. Individual-level survey data, IHME/GBD, subnational data and streaming are out of scope ([SCOPE.md](SCOPE.md)).

The source-profiling pass ([source_profile.md](source_profile.md)) shaped the framing before any pipeline was written. Raw source quality is already high (values are almost all valid and in range), so the contribution is **provenance-aware integration** and an honest measurement of what validation adds, not a claim that the sources are dirty. Architecture, layer guarantees and design decisions: [architecture.md](architecture.md). Table and column reference: [data_dictionary.md](data_dictionary.md).

## 3. Method in brief

- **Ingestion.** Responses are stored byte for byte with a manifest (URL, status, size, SHA-256). A series is written only if fully downloaded. Requests are paced per source and retried.
- **Staging.** Typed, not cleaned. Every row records its snapshot, file and row number. A failed checksum refuses the load; loads are idempotent and transactional.
- **Core.** Canonical dimensions and a fact table at an explicit grain (source, country, indicator, year, sex, wealth quintile, residence, maternal education, age group, upstream series). Rows that cannot be loaded are stored with a reason (3,478 of 90,503, all empty World Bank placeholders). A default analyst view selects one national-total row per source and cell by a documented rule. Rules and their evidence: [harmonization.md](harmonization.md).
- **Source dependence is derived from the data.** Two sources are treated as one evidence group when at least 30 shared country-years agree within 1% in at least 90% of cases. The reconciled value is the highest-priority source's value from the highest-priority group; it is never an average. Conflicts between independent groups above 10% are flagged and each group's value is kept.
- **Measurement.** Completeness, validity, uniqueness, consistency and integration are reported separately; the composite is an unweighted mean with a leave-one-dimension-out range because the weights are arbitrary.
- **Experiments.** Fault injection and the ablation corrupt a *copy* of the snapshot at known positions and run the unchanged pipeline on it. Rates are pooled over seeds with Wilson intervals; resampling is by country.
- **Pre-registration.** The ML gate criteria, the model protocol and the ablation protocol were committed before the corresponding results were generated ([ml_decision.md](ml_decision.md)). Anything decided after seeing results is listed there.

## 4. Integration results (RQ1)

Full report: [results/before_after.md](results/before_after.md). Before is the staged data an analyst would first query; after is the default core surface.

| | Before | After |
|---|---|---|
| Rows unique at the (country, indicator, year) grain | 30.5% | 100% (by construction) |
| Join fan-out, under-5 mortality / stunting | 57.9x / 228.3x | 1.0x |
| "Conflicting" rows, stunting join | 82.3% (mostly breakdowns against totals) | 16.0% (genuine disagreement) |
| Concepts with independent cross-validation | not measurable | 1 of 7 |
| Records rejected | not tracked | 3,478, each with a reason |

Two cautions. First, validity, uniqueness and consistency reach 100% after the pipeline largely *by construction*; those scores show that the pipeline enforces the properties, not that the data improved in a way nothing enforced. The independent evidence is the join behaviour, coverage and the source-dependence analysis. Second, coverage cannot be improved by cleaning: the pipeline neither adds nor, with one trivial exception (country-years that exist only as a breakdown), removes country-years. Skilled birth attendance remains a median of 3 years out of date, with 37% of countries 5 or more years behind.

## 5. Validation results (RQ2)

Full report: [results/fault_injection.md](results/fault_injection.md). 11,030 faults across 5 seeds and 15 corrupted snapshots; the clean universe of 29,099 analysis rows supplies the false-alarm counts.

- **Rule-based faults** (missing, unparseable, out of range, duplicate, recoverable or invalid country, bad date, bad vocabulary): 100% handled as expected. This was designed to hold. It shows that the rules work end to end through the real pipeline, including repair of recoverable country identifiers (lower case, padding, ISO2, name, alias), not that unanticipated faults would be caught.
- **Value changes of known size** are the informative result. Detection by any check: 0.0% at 2%, 56.8% at 5%, 96.0% at 10%, 100% at 25% to 100%, 99.6% at 300%. The 5% row is a threshold effect: increases are flagged 15.5% of the time, decreases 96.1%, because the disagreement threshold divides by the group mean.
- **Each statistical check is reported on the rows it can assess.** The cross-source disagreement check caught 100% of changes of 10% or more where another source in the group existed (about 95% of rows). The temporal outlier check, which needs at least 3 neighbouring observations (about 92% of rows), rose from 58% at +25% to 97% at +900%.
- **False alarms.** On the unmodified data the outlier check flags 0.85% of rows and the disagreement check 1.06% of cells; those alerts are unlabeled and include real events. In the experiment, outlier precision was 92.3% (83 false positives), traced in a seed-1 diagnosis to a side effect of rejection: removing a bad row removes it from its neighbours' context.
- **Naive loader** (parse, exact ISO3 join, drop nulls): it accepted every out-of-range value, every duplicate and every value change. The pipeline let 18.4% of the 2,000 value changes through, nearly all at 10% or below.
- **Not covered:** valid-looking corruptions (a swapped but valid country or sex code; a small change with no context and no second source) and error kinds not injected.

## 6. Downstream results (RQ3)

Machine learning was a downstream demonstration and was gated on the data. Reports: [ml_gate.md](results/ml_gate.md), [ml_models.md](results/ml_models.md), [ml_ablation.md](results/ml_ablation.md); protocol and outcomes: [ml_decision.md](ml_decision.md).

**Gate.** Rolling-origin backtests of three simple baselines (last value, linear trend over five observations, average change) cannot see data after their origin; a country-level bootstrap gives intervals. Four indicators qualify (under-5 mortality, maternal mortality, DTP3, measles). Three do not: neonatal mortality and stunting because the best baseline's error is under the 5% headroom threshold (stunting misses by 0.4 percentage points, so that call is close) and skilled birth attendance because only 26 countries have 8 or more observations. No single baseline wins.

**Models.** On 10,072 forecasts from 54 countries and five cut-offs, gradient boosting at 4-5 years beat the best baseline for under-5 mortality (-2.83 pp, interval -4.30 to -1.61) and maternal mortality (-3.50 pp, -5.42 to -1.98), at all five cut-offs. For DTP3 (+0.47 pp) and measles (+0.08 pp) it did not. At one year a linear trend is better. The pooled ridge model was not useful anywhere and fits poorly in sample too; it was reported as pre-registered.

**Ablation.** The same task was run on data corrupted at 1%, 3% and 10% of rows (three seeds each), prepared three ways: clean through the pipeline, corrupted with a minimal naive loader, and corrupted through the pipeline. Models trained on each variant's data and were scored against the clean truth.

| Corruption | Naive: loss in accuracy | Pipeline: loss in accuracy | Difference (95% interval) |
|---|---|---|---|
| 1% | +0.25 pp | +0.00 pp | +0.26 pp (+0.02 to +0.47) |
| 3% | +0.55 pp | +0.00 pp | +0.55 pp (+0.34 to +0.75) |
| 10% | +0.95 pp | +0.16 pp | +0.73 pp (+0.33 to +1.10) |

The pre-registered primary outcome was met, and the result holds across corruption seeds (at 10%, naive error 7.0-7.3% against 5.6-5.9% through the pipeline). It is small, and three things go against the pipeline:

- **Coverage.** The pipeline can forecast fewer series: 77.8% of the clean forecasts at 10% corruption, against 89.3% for the naive variant. The accuracy figures use only forecasts present in all three variants, so this cost is not in them.
- **Under-5 mortality shows no benefit.** The interval includes zero at 3% and 10%, and at 1% it favours the naive variant. The pipeline's flagging also removes genuine abrupt changes; this is a plausible explanation, not a tested one.
- **The pre-registered median metric is uninformative for the baselines.** It shows 0.00 everywhere because fewer than half of baseline forecasts are touched. A post-hoc check on single corruptions found a heavy tail (a mean increase of about 25 points at 10%), so baselines are badly hurt where corruption lands.

## 7. Limitations and threats to validity

- **Synthetic faults.** Injected faults are proxies. Their mixture, rates and magnitudes are choices, and the "naive" loader is deliberately minimal; a careful analyst would do better than it. Rates describe this pipeline on this data, not data quality in general.
- **Judgement thresholds.** Plausible ranges, headline definitions, tolerances, outlier parameters and the ML gate thresholds were fixed in advance but are judgements. The gate's 5% threshold cuts through a continuum.
- **Modelled estimates.** Most series are model outputs (UN IGME, WUENIC, MMEIG), not observations. Forecast skill is skill at extrapolating another model's estimates, and because the panel is final-vintage, the backtests are pseudo out-of-sample: real-time error would be larger, especially at long horizons.
- **Dependence inferred from values.** Source independence is derived from agreement, not documentation; two sources could agree by coincidence or differ for benign reasons such as rounding or vintage.
- **Unverified extremes.** Large short-run rises in under-5 mortality in a few conflict-affected countries (South Sudan, Central African Republic, Libya, Somalia) were not checked against source documentation. The outlier check flags single-year spikes but not multi-year shifts.
- **One snapshot.** Estimates are revised between releases. Three corruption seeds per rate cover few corruption patterns; the country bootstrap covers sampling of countries, not of corruption patterns.
- **Ecological associations.** Cross-country correlations (for example -0.74 between under-5 mortality and DTP3 coverage) are descriptive, treat countries as independent observations, and say nothing about cause.
- **Shocks.** DTP3 and measles coverage fell in 2020-2021, which inflates errors for every method.

## 8. Ethics

Only aggregate, public, non-personal data is used. Country-level differences are preserved and comparison limits are documented. The dashboard never ranks countries, shows the quality tier of every series, carries an "associations, not causes" notice on relationships, and offers a table view of every chart. Regional figures summarize countries, not people. Nothing here is clinical or policy advice, and the forecasts are a methodological demonstration.

## 9. Reproducing the work

```bash
cp .env.example .env && docker compose up -d db
pip install -e ".[dev,dashboard,ml]"
python -m healthbridge.ingest run && python -m healthbridge.staging load
python -m healthbridge.dq baseline                              # staging, the 'before'
python -m healthbridge.core build
python -m healthbridge.dq baseline --stage core                 # core, the 'after'
python -m healthbridge.marts build
python -m healthbridge.dq compare --out docs/results/before_after.md
python -m healthbridge.experiments run --seeds 5 --resume       # about 25 minutes
python -m healthbridge.ml gate && python -m healthbridge.ml models
python -m healthbridge.ml ablation run                          # about 2 hours
pytest
```

Re-running ingestion creates a new snapshot, so numbers will change as the sources revise their estimates; the snapshot identifier is printed in every report. The 144 tests run in CI against PostgreSQL on every pull request.

## 10. What a next step would be

Orchestration with scheduling and retries; freshness and drift monitoring across releases (which would also address the single-snapshot limit); documented-provenance checks to confirm the inferred source dependence; a verification pass on the extreme mortality values; and a real-vintage backtest using archived earlier releases, which would replace pseudo out-of-sample with true out-of-sample forecasts.
