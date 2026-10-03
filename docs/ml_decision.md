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
