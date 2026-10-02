# Analytics marts: definitions, grains and caveats

The marts are the analysis-ready tables built from the core layer. They are rebuilt for one snapshot at a time (`python -m healthbridge.marts build`), every table carries its `run_id`, and the parameters used are recorded in `marts.build_log`. The SQL is in [`sql/marts/`](../sql/marts); a generated report with the results on the real snapshot is [results/analytics.md](results/analytics.md).

## Principles

1. **Trust travels with the number.** Every value in the panel says whether it could be cross-checked, so an analysis can filter on it instead of assuming all values are equally reliable.
2. **Descriptive, not causal.** Associations are cross-country, ecological and rank-based. They cannot show that one indicator drives another.
3. **Countries stay separate.** Regional summaries always carry the range and coverage next to the average, and no table ranks countries as better or worse.
4. **Nothing is invented.** Reconciled values are never averages; missing country-years stay missing.

## Tables

| Table | Grain | What it is |
|---|---|---|
| `country_indicator_year` | run, country, indicator, year | The reconciled value, its source, the number of sources and evidence groups, a quality tier, and the outlier and source-disagreement flags |
| `country_latest` | run, country, indicator | The most recent value, its age relative to the freshest country, and the length of history behind it |
| `trend` | run, country, indicator | Change over the window from 2000: linear slope, log-linear annual rate, absolute change |
| `region_year` | run, UN sub-region, indicator, year | Unweighted mean, median, quartiles and range across countries, with coverage |
| `indicator_association` | run, indicator pair, basis | Spearman rank correlation across countries, with an approximate interval |
| `equity_gap` | run, country, indicator, year, dimension | Ratio and difference between two groups of the same survey or series |
| `data_trust` | run, indicator | Coverage and the mix of quality tiers |

Views `marts.v_panel` and `marts.v_country_latest` attach names and regions and always read the most recently built snapshot.

## Definitions

**Quality tier.** `cross_validated`: two or more independent evidence groups reported the value and agreed within tolerance. `single_evidence_group`: all sources republish one estimate, so the value cannot be cross-checked (agreement between WHO, UNICEF and the World Bank is not independent confirmation; see [harmonization.md](harmonization.md), D7). `conflict`: independent groups differ by more than 10%. `outlier_flag`: the reconciled source's row deviates from its neighbouring years. `source_disagreement`: sources of the same group differ by more than 5% in that cell.

**Latest value and staleness.** `years_behind` is the latest year available for the indicator anywhere minus the country's latest year, so an old survey value is not mistaken for a current one.

**Trend.** Computed on each country's own series from 2000, only with at least 4 observations spanning at least 8 years. The *linear slope* is in units per year and suits percentages. The *annual percentage change* comes from a regression of the logarithm on the year and is defined only when every value is positive; it suits rates that fall proportionally (mortality), and is shown for all indicators only where defined. Neither is a forecast.

**Regional summaries.** Unweighted, over the countries with a value in that year. They describe the average country, not the average person, because no population data is included yet. The country set can change between years, so `n_countries` and `coverage_share` accompany every row and years should be compared only where coverage is similar.

**Associations.** Spearman correlation (average ranks for ties) between two indicators across countries.
- *Level:* each country contributes its observation nearest the reference year (2015) within 3 years, so series published for different years are not compared at far-apart dates. Conflicted values are excluded.
- *Change:* each country contributes the difference between its observation near 2015 and near 2000, with the two ends at least 8 years apart. Rank correlation lets indicators in different units be compared.
- The interval is the Fisher-z approximation, and assumes independent countries (they are not; see below).

**Equity gaps.** Pair two groups from the *same* source, upstream label and period, with every other dimension at its total and with the indicator's defined age group: poorest vs richest wealth quintile, female vs male, rural vs urban. Comparing groups from different surveys would compare unlike things. Where several series offer a pair for a country-year, one is chosen by source priority (WHO, then UNICEF), then the latest period; `n_candidate_pairs` records how many were available.

## Caveats a reader needs

- **Ecological, not individual.** A correlation across countries says nothing about the individuals within them, and countries that share a region, income level or history are not independent observations, so the intervals understate the uncertainty.
- **Built-in relationships.** Some pairs are related by definition (neonatal deaths are part of under-5 deaths) or by delivery (DTP3 and measles vaccines are given through the same programme). Their strong correlations are not informative.
- **Modelled estimates.** Most mortality and immunization series are model-based, smooth by construction, and revised between releases. Results depend on the snapshot date.
- **Sustained shocks versus errors.** The outlier check flags isolated deviations; a multi-year shift (for example during conflict) is not flagged. Neither pattern has been verified against source documentation, and the report says so.
- **Source priority.** Equity gaps mix modelled (WHO) and survey (UNICEF) series by a fixed priority rule; survey gaps rest on small samples.
- **No weights, no causation, no forecasts.** Population weighting, causal inference and forecasting are outside this release.

## Parameters

Recorded with every build (`marts.build_log.parameters`): trend window start (2000), minimum points (4) and span (8 years), reference year (2015), base year (2000), tolerance (3 years), minimum span for a change (8 years), and minimum countries for an association (10). They are judgements, not estimates, and are exposed as arguments to `build_marts` so their effect can be tested.
