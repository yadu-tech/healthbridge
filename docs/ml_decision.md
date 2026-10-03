# Machine-learning decision gate

The machine-learning component is a downstream demonstration of what reliable data infrastructure makes possible. It is not the point of the project, and it is only done if the data supports it. This document is the protocol for deciding, **written before any model or backtest was run**, so that the thresholds cannot be adjusted afterwards to reach a more interesting answer. The results and the decision are appended below in the section "Outcome".

## Candidate task

**Forecasting a country's value of an indicator** a number of years ahead, from its own history and, for pooled models, from other countries and indicators. This is the only family considered, because the integrated data is an annual country-year panel of aggregate indicators. Individual-level prediction is out of scope and the data does not support it. Clustering and anomaly detection were considered and rejected as the main task: anomaly detection is already covered by the quality checks and evaluated in the fault-injection experiment, and clustering has no ground truth to evaluate against.

## What the gate measures

Per indicator, on the reconciled panel (values whose quality tier is not `conflict`):

1. **Data volume.** How many countries have enough history to train and test a model.
2. **Baseline difficulty (headroom).** Rolling-origin backtests of simple baselines: *last value*, *linear trend over the last five observations*, and *average annual change over the whole history*. Every forecast uses only data up to its origin year, and is clipped to the indicator's plausible range. If a trivial baseline is already near-perfect, a learned model has nothing to add.
3. **Evaluability.** Whether there are enough backtest instances, from enough independent countries, to compare models honestly.

Errors are absolute percentage errors, summarised as the median across instances, with a 95% interval from a bootstrap that resamples **countries** (not instances), because countries are the independent units. Horizons are the number of years between the origin observation and the target observation, grouped as 1, 2-3, 4-5 and 6-10 years.

## Criteria (fixed in advance)

An indicator **supports a forecasting task** only if all three hold:

| Criterion | Threshold | Reason |
|---|---|---|
| C1 Data | at least 30 countries with at least 8 observations | pooled models need cross-country training data |
| C2 Headroom | best baseline's median absolute percentage error at the 4-5 year horizon is at least 5% | below this the series is so smooth that a learned model cannot add meaningful value, and gains would be within the noise of the estimates themselves |
| C3 Evaluable | at least 200 backtest instances from at least 20 countries at the 4-5 year horizon | needed to tell models apart without leaning on a few countries |

**Decision rule.** If at least one indicator meets all three, the forecasting task is run on those indicators only. If none do, **no machine-learning model is built**, the reason is documented, and the project reports that the data does not support it. The thresholds are judgements, recorded here so they can be questioned.

## Ethical and validity requirements for a "go"

- Forecasts are of aggregate, public, modelled estimates. They are illustrative, are never presented as predictions for policy, and always carry uncertainty.
- No country ranking and no "good or bad" labelling; results are shown by indicator and horizon.
- Models are evaluated only with rolling-origin backtests (no random splits, which leak the future) and leave-country-out checks for pooled models; a model must beat the best baseline to be reported as useful.
- Most series here are themselves model outputs (smooth by construction), so skill at forecasting them is skill at extrapolating another model, and is reported as such.

## The research link (if go)

The model comparison is run on the integrated panel. The question that connects it to the data infrastructure is then: **how much does forecast accuracy and reproducibility depend on data quality?** The same forecasting task is run on (a) the integrated, validated data, (b) corrupted data processed naively with no validation, and (c) corrupted data processed by the pipeline, all scored against the clean truth. That ablation is the evidence about reliable infrastructure and downstream AI.

## Outcome (gate run on snapshot `20261001T110703Z`)

Full tables: [results/ml_gate.md](results/ml_gate.md). **Decision: go, for four indicators** (DTP3 coverage, measles MCV1 coverage, maternal mortality ratio, under-5 mortality). Three indicators do not qualify:

| Indicator | Verdict | Why |
|---|---|---|
| Under-5 mortality | go | best baseline error 6.6% at 4-5 years (interval 5.2-8.4%) |
| Maternal mortality ratio | go | 12.6% |
| DTP3 coverage | go | 6.8% (5.3-8.7%) |
| Measles (MCV1) coverage | go | 7.2% (6.0-8.9%) |
| Neonatal mortality | no | 3.7% (2.9-4.6%): below the 5% headroom threshold |
| Stunting | no | 4.6% (3.9-5.4%): below the threshold, and its interval straddles it |
| Skilled birth attendance | no | fails all three: only 26 countries have 8 or more observations and 164 instances at 4-5 years; it is sparse (median gap 3 years) |

### What the result does and does not say

- **The thresholds fall inside a continuum.** Stunting misses the 5% line by 0.4 percentage points and under-5 mortality clears it by 1.6. The criteria were fixed in advance and are applied as written, but a different threshold would have drawn the line differently, and the decision for stunting in particular is close.
- **The indicators that qualify are not the sparse ones.** The sparse survey indicator the project might most like to model fails on data volume. The four that qualify are annual series that are themselves model outputs.
- **No single baseline wins.** Repeating the last value is best for the noisy coverage series, a linear trend is best for the smoothly declining mortality series. Any model has to be compared against the better baseline per indicator and horizon.
- **Vintage leakage (a threat to validity).** The panel is final-vintage modelled estimates. An estimate for 2010 was produced with data from after 2010, so even though no backtest forecast uses data after its origin year, the *history* it does use carries some information about later years. Real-time forecast error would be larger than any backtest figure here, and the backtest should be read as "pseudo out-of-sample".
- **Shocks.** DTP3 and measles coverage fell in 2020-2021; targets in those years inflate their errors for every method. Conflict-affected series have sustained shifts no extrapolation anticipates.
- **Forecasting a model's output.** Skill here is skill at extrapolating another model's estimates, not at predicting underlying health outcomes.

## Stage 2 protocol: models and the data-quality ablation (fixed before any model is built)

**Indicators:** the four above. **Models:** the three baselines; a regularised linear model (ridge); and gradient-boosted trees, both pooled across countries and indicators.

**Evaluation.** Panel-level rolling origin: for each cut-off year T in {2005, 2008, 2011, 2014, 2017}, a model is trained only on observations whose *target* year is at most T, then forecasts from origins in year T to targets at horizons 1 to 10. Training never sees any year after T. Errors are absolute percentage errors, grouped by horizon as above. Gradient boosting is run with several random seeds and its seed-to-seed spread is reported.

**Features** use only information available at the origin: the latest value, recent changes over 3 and 5 observations, the history's average change, the horizon, the sub-region, and the latest values and recent changes of the *other* indicators for the same country.

**A model is reported as useful for an indicator only if** its median absolute percentage error at the 4-5 year horizon is below the best baseline's, **and** the 95% interval of the paired difference (resampling countries) lies entirely below zero. Models that do not clear this are reported as not beating the baseline. A negative result is a result.

**Data-quality ablation (the research link).** The same forecasting task is run on (a) the integrated, validated data; (b) a snapshot corrupted by the fault-injection harness and processed naively, with no validation; and (c) the same corrupted snapshot processed by the pipeline. All three are scored against the clean truth. The outcome measures are forecast error, and the spread of results across seeds, as a function of the fault rate.

## Stage 2 outcome: the model comparison (snapshot `20261001T110703Z`)

Full tables: [results/ml_models.md](results/ml_models.md). 10,072 test forecasts from 54 countries, five cut-offs, four indicators. The success rule was applied exactly as written above.

| Indicator | Gradient boosting at 4-5 years | Ridge |
|---|---|---|
| Under-5 mortality | **useful**: -2.83 pp against the best baseline (95% interval -4.30 to -1.61) | not useful |
| Maternal mortality ratio | **useful**: -3.50 pp (-5.42 to -1.98) | not useful |
| DTP3 coverage | not useful: typically worse than repeating the last value (+0.47 pp, +0.08 to +0.95) | not useful |
| Measles (MCV1) coverage | not useful: tied (+0.08 pp, -0.24 to +0.58) | not useful |

**What this says.**
- Gradient boosting beats the best simple baseline on the two mortality indicators at **every one of the five cut-offs**, so the result is not an artefact of one period. For the two immunization indicators it wins at 3 of 5 cut-offs and the paired interval is not below zero, so the pre-registered rule does not call it useful.
- The advantage is a long-horizon one. At one year the simple linear trend is better (under-5 mortality: 1.3% against 3.1% for gradient boosting); at 6-10 years boosting is better by about 8 percentage points for both mortality indicators. A pooled model that has seen how mortality declines across countries helps most where straight-line extrapolation drifts.
- Seed-to-seed spread of gradient boosting at 4-5 years is at most 0.4 percentage points, so seed noise is not driving the verdict.
- The ridge model is not useful for any indicator. It is poor in sample as well as out of sample (7-11% error at one year against 3-4% for a trivial baseline), which points to a poor fit of one pooled linear model to four differently moving indicators, not to leakage or overfitting.

**What it does not say.** These are pseudo out-of-sample results on final-vintage modelled estimates (see the vintage-leakage note above), so real-time skill would be lower and the long-horizon gains in particular should be read with that in mind. It is skill at extrapolating another model's estimates, not at predicting health outcomes, and it is not a basis for policy or for ranking countries.

### What was decided after seeing results (disclosure)

- **Fixed in advance and used as written:** the indicators, models, cut-offs, features, success rule and hyperparameters. Hyperparameters were set before any run and not tuned.
- **Implementation choices made before the first run, not in the protocol text:** the gradient-boosting headline forecast is the mean of the five seeds, with each seed's own error reported as a stability check; interaction terms between the horizon and recent changes were added as derived features; the best baseline is chosen per indicator and horizon on the test data, which favours the baseline.
- **Added after seeing the first results, and explanatory only:** the per-cut-off table and the ridge fit check. They do not change any verdict. No model was altered, retuned or dropped in response to a result, including the ridge model.

The data-quality ablation (does corrupted, unvalidated data degrade these forecasts, and does the pipeline prevent it?) remains the next step.
