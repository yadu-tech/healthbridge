# Analytics report: snapshot `20261001T110703Z`

Generated from the marts built 2026-10-02 19:06 UTC with `python -m healthbridge.marts report`. Definitions, grains and caveats are in [../analytics.md](../analytics.md). Parameters: trend window from 2000; cross-country comparisons use the observation nearest 2015 within +-3 years (changes: 2000 to 2015).

**How to read this.** Everything here is descriptive. Associations are between countries (ecological), cannot show cause, and are partly shaped by shared geography, income and history. Averages are of countries, not people. Many series are modelled estimates, and most indicators cannot be cross-checked because the sources republish one estimate.

## 1. How far each indicator can be trusted

| Indicator | Countries | Years | Grid coverage | Single evidence group | Cross-validated | Conflict | Outlier-flagged | Source disagreement |
|---|---|---|---|---|---|---|---|---|
| dtp3_coverage | 54 | 1990-2025 | 95.1% | 100.0% | 0.0% | 0.0% | 1.37% | 1.68% |
| maternal_mortality_ratio | 54 | 1990-2023 | 91.9% | 100.0% | 0.0% | 0.0% | 1.03% | 3.05% |
| measles_mcv1_coverage | 54 | 1990-2025 | 95.1% | 100.0% | 0.0% | 0.0% | 0.95% | 1.37% |
| neonatal_mortality | 54 | 1990-2024 | 94.6% | 100.0% | 0.0% | 0.0% | 0.00% | 0.00% |
| skilled_birth_attendance | 54 | 1990-2024 | 22.6% | 100.0% | 0.0% | 0.0% | 2.00% | 0.89% |
| stunting_prevalence | 54 | 1990-2024 | 71.6% | 77.2% | 19.2% | 3.6% | 0.00% | 0.14% |
| under5_mortality | 54 | 1990-2024 | 94.6% | 100.0% | 0.0% | 0.0% | 0.63% | 0.00% |

*Single evidence group* means every source republishes one estimate, so agreement between them is not independent confirmation. Only stunting has independent evidence (model-based WHO against survey-based UNICEF and World Bank values).

## 2. Regional spread: countries within a region differ widely

Median and range across countries in each UN sub-region, for the indicators with annual series. The range matters as much as the median: sub-regions are not homogeneous.

**Under-5 mortality (deaths per 1000 live births)**

| Sub-region | 2000: median (range) | 2015: median (range) |
|---|---|---|
| Eastern Africa | 134.1 (13.8-184.2), n=18/18 | 58.6 (14.5-240.8), n=18/18 |
| Middle Africa | 154.5 (73.3-185.0), n=9/9 | 88.5 (23.3-128.7), n=9/9 |
| Northern Africa | 44.5 (28.1-102.0), n=6/6 | 24.0 (13.3-63.5), n=6/6 |
| Southern Africa | 79.8 (71.4-112.5), n=5/5 | 51.8 (38.2-75.1), n=5/5 |
| Western Africa | 153.1 (36.0-227.9), n=16/16 | 92.4 (19.2-136.8), n=16/16 |

**Maternal mortality ratio (per 100000 live births)**

| Sub-region | 2000: median (range) | 2015: median (range) |
|---|---|---|
| Eastern Africa | 515.9 (43.3-1657.8), n=18/18 | 377.3 (46.5-1435.6), n=18/18 |
| Middle Africa | 585.4 (178.1-1489.7), n=9/9 | 284.1 (127.8-1176.0), n=9/9 |
| Northern Africa | 96.8 (52.8-645.5), n=6/6 | 77.8 (24.3-297.9), n=6/6 |
| Southern Africa | 360.7 (135.7-412.6), n=5/5 | 283.0 (140.5-593.9), n=5/5 |
| Western Africa | 692.7 (110.2-1603.1), n=16/16 | 497.4 (46.4-1167.7), n=16/16 |

**DTP3 coverage (% of one-year-olds)**

| Sub-region | 2000: median (range) | 2015: median (range) |
|---|---|---|
| Eastern Africa | 78.0 (30.0-98.0), n=17/18 | 88.5 (46.0-98.0), n=18/18 |
| Middle Africa | 37.0 (30.0-82.0), n=9/9 | 70.0 (40.0-96.0), n=9/9 |
| Northern Africa | 94.5 (62.0-98.0), n=6/6 | 96.0 (93.0-99.0), n=6/6 |
| Southern Africa | 84.0 (73.0-97.0), n=5/5 | 90.0 (83.0-95.0), n=5/5 |
| Western Africa | 50.0 (29.0-90.0), n=16/16 | 83.0 (42.0-97.0), n=16/16 |

## 3. Direction and pace of change since 2000

Each country's slope over its own series (at least 4 observations spanning 8 years or more). Linear slope is in the indicator's units per year; the annual percentage change is log-linear and defined only for positive series.

| Indicator | Countries | Median slope per year | Median annual % change | Countries falling | Countries rising |
|---|---|---|---|---|---|
| dtp3_coverage | 54 | 0.26 | 0.34% | 24% | 76% |
| maternal_mortality_ratio | 54 | -11.04 | -3.00% | 91% | 9% |
| measles_mcv1_coverage | 54 | 0.37 | 0.52% | 22% | 78% |
| neonatal_mortality | 54 | -0.51 | -1.78% | 94% | 6% |
| skilled_birth_attendance | 45 | 1.23 | 1.92% | 2% | 98% |
| stunting_prevalence | 54 | -0.57 | -2.14% | 96% | 4% |
| under5_mortality | 54 | -2.70 | -3.52% | 96% | 4% |

## 3b. Reversals: under-5 mortality that rose over four years

Countries whose reconciled under-5 mortality was more than 10% higher than four years earlier at some point since 2000 (the largest such rise per country). Two patterns appear. A **sustained** rise over several years is not flagged by the temporal outlier check, which compares each year with its neighbours. A **single-year spike** is flagged; the last column counts flagged years inside the span so the two can be told apart. Flagged rows stay in the data. All sources agree because they republish one estimate, which says nothing about whether the values are right. **These values have not been verified against the source documentation** and should be checked before any is used in an analysis; they also account for extreme values in the regional ranges above.

| Country | From | To | Rise | Outlier flags in span |
|---|---|---|---|---|
| Central African Republic | 120.2 (2018) | 424.6 (2022) | 253% | 2 |
| South Sudan | 97.2 (2013) | 292.0 (2017) | 201% | 0 |
| Libya | 11.6 (2019) | 31.7 (2023) | 174% | 1 |
| Somalia | 170.5 (2007) | 363.1 (2011) | 113% | 1 |
| Ethiopia | 55.8 (2018) | 68.1 (2022) | 22% | 1 |
| Zimbabwe | 56.3 (2018) | 64.0 (2022) | 14% | 0 |
| Sudan | 54.3 (2020) | 61.6 (2024) | 13% | 0 |
| South Africa | 71.4 (2000) | 80.7 (2004) | 13% | 0 |

## 4. Associations between indicators across countries

Spearman rank correlation with an approximate 95% interval (Fisher z). **Associations only.** Levels compare countries at one time; changes compare how countries moved. Pairs marked * are partly built in.

**Levels (nearest observation to the reference year)**

| Pair | Countries | Spearman rho | 95% interval |
|---|---|---|---|
| dtp3_coverage / measles_mcv1_coverage* | 54 | 0.95 | 0.92 to 0.97 |
| under5_mortality / neonatal_mortality* | 54 | 0.90 | 0.83 to 0.94 |
| under5_mortality / maternal_mortality_ratio | 54 | 0.81 | 0.70 to 0.89 |
| neonatal_mortality / maternal_mortality_ratio | 54 | 0.80 | 0.68 to 0.88 |
| under5_mortality / measles_mcv1_coverage | 54 | -0.75 | -0.85 to -0.61 |
| under5_mortality / dtp3_coverage | 54 | -0.74 | -0.84 to -0.58 |
| maternal_mortality_ratio / skilled_birth_attendance | 49 | -0.71 | -0.82 to -0.53 |
| neonatal_mortality / skilled_birth_attendance | 49 | -0.69 | -0.81 to -0.51 |
| neonatal_mortality / measles_mcv1_coverage | 54 | -0.69 | -0.81 to -0.51 |
| neonatal_mortality / dtp3_coverage | 54 | -0.69 | -0.81 to -0.51 |
| under5_mortality / skilled_birth_attendance | 49 | -0.68 | -0.81 to -0.50 |
| under5_mortality / stunting_prevalence | 54 | 0.63 | 0.43 to 0.77 |
| maternal_mortality_ratio / dtp3_coverage | 54 | -0.62 | -0.76 to -0.42 |
| maternal_mortality_ratio / measles_mcv1_coverage | 54 | -0.61 | -0.76 to -0.41 |
| skilled_birth_attendance / measles_mcv1_coverage | 49 | 0.59 | 0.38 to 0.75 |
| maternal_mortality_ratio / stunting_prevalence | 54 | 0.59 | 0.38 to 0.74 |
| skilled_birth_attendance / dtp3_coverage | 49 | 0.56 | 0.33 to 0.73 |
| skilled_birth_attendance / stunting_prevalence | 49 | -0.50 | -0.69 to -0.26 |
| neonatal_mortality / stunting_prevalence | 54 | 0.46 | 0.22 to 0.65 |
| dtp3_coverage / stunting_prevalence | 54 | -0.41 | -0.61 to -0.16 |
| measles_mcv1_coverage / stunting_prevalence | 54 | -0.40 | -0.60 to -0.14 |

**Changes (reference year minus base year)**

| Pair | Countries | Spearman rho | 95% interval |
|---|---|---|---|
| dtp3_coverage / measles_mcv1_coverage* | 53 | 0.71 | 0.55 to 0.82 |
| under5_mortality / neonatal_mortality* | 54 | 0.66 | 0.47 to 0.79 |
| neonatal_mortality / maternal_mortality_ratio | 54 | 0.60 | 0.39 to 0.74 |
| under5_mortality / dtp3_coverage | 53 | -0.54 | -0.71 to -0.31 |
| under5_mortality / maternal_mortality_ratio | 54 | 0.47 | 0.23 to 0.65 |
| maternal_mortality_ratio / dtp3_coverage | 53 | -0.47 | -0.65 to -0.22 |
| neonatal_mortality / dtp3_coverage | 53 | -0.45 | -0.64 to -0.20 |
| maternal_mortality_ratio / measles_mcv1_coverage | 53 | -0.42 | -0.62 to -0.16 |
| neonatal_mortality / stunting_prevalence | 54 | 0.41 | 0.16 to 0.61 |
| under5_mortality / skilled_birth_attendance | 47 | -0.40 | -0.62 to -0.13 |
| neonatal_mortality / skilled_birth_attendance | 47 | -0.40 | -0.62 to -0.13 |
| under5_mortality / stunting_prevalence | 54 | 0.39 | 0.14 to 0.60 |
| maternal_mortality_ratio / skilled_birth_attendance | 47 | -0.36 | -0.59 to -0.09 |
| under5_mortality / measles_mcv1_coverage | 53 | -0.32 | -0.55 to -0.06 |
| skilled_birth_attendance / stunting_prevalence | 47 | -0.31 | -0.55 to -0.03 |
| skilled_birth_attendance / measles_mcv1_coverage | 47 | 0.24 | -0.06 to 0.49 |
| neonatal_mortality / measles_mcv1_coverage | 53 | -0.21 | -0.45 to 0.07 |
| dtp3_coverage / stunting_prevalence | 53 | -0.18 | -0.43 to 0.10 |
| maternal_mortality_ratio / stunting_prevalence | 54 | 0.15 | -0.12 to 0.40 |
| skilled_birth_attendance / dtp3_coverage | 47 | 0.12 | -0.18 to 0.39 |
| measles_mcv1_coverage / stunting_prevalence | 53 | -0.11 | -0.37 to 0.16 |

- \* neonatal_mortality and under5_mortality: neonatal deaths are a subset of under-5 deaths.
- \* dtp3_coverage and measles_mcv1_coverage: delivered together through the same routine immunization programme.

## 5. Equity gaps

Gaps use only groups from the same survey or series, with each country's most recent available gap. *Ratio* is the first group over the second. Modelled WHO series are preferred over survey series where both exist (a documented source-priority rule).

| Dimension | Indicator | Ratio of | Countries | Median ratio | Min | Max | Share above 1 |
|---|---|---|---|---|---|---|---|
| residence | skilled_birth_attendance | rural / urban | 52 | 0.85 | 0.23 | 1.01 | 4% |
| residence | stunting_prevalence | rural / urban | 52 | 1.50 | 1.00 | 2.11 | 100% |
| sex | stunting_prevalence | female / male | 54 | 0.83 | 0.77 | 0.93 | 0% |
| sex | under5_mortality | female / male | 54 | 0.84 | 0.77 | 0.93 | 0% |
| wealth | skilled_birth_attendance | poorest / richest | 50 | 0.72 | 0.10 | 0.98 | 0% |
| wealth | stunting_prevalence | poorest / richest | 51 | 2.11 | 1.03 | 5.61 | 100% |
| wealth | under5_mortality | poorest / richest | 48 | 1.70 | 1.14 | 2.53 | 100% |

## 6. How current is the latest value?

Years between each country's latest value and the most recent year available for that indicator in any country. Survey-based series lag.

| Indicator | Countries | Median years behind | Max years behind | 5+ years behind |
|---|---|---|---|---|
| dtp3_coverage | 54 | 0 | 0 | 0% |
| maternal_mortality_ratio | 54 | 0 | 0 | 0% |
| measles_mcv1_coverage | 54 | 0 | 0 | 0% |
| neonatal_mortality | 54 | 0 | 0 | 0% |
| skilled_birth_attendance | 54 | 3 | 14 | 37% |
| stunting_prevalence | 54 | 0 | 0 | 0% |
| under5_mortality | 54 | 0 | 0 | 0% |

## 7. Limitations

- Cross-country associations are ecological: they describe countries, not individuals or mechanisms, and share geography and history, so the effective sample is smaller than the country count suggests.
- Most series are modelled estimates. Where WHO, UNICEF and the World Bank agree they are usually one estimate, not three.
- Regional averages are unweighted (the average country, not the average person); no population data is included in this release.
- Equity gaps mix modelled (WHO) and survey (UNICEF) series by a priority rule, and survey gaps rest on small samples.
- Nearest-observation alignment within +-3 years treats values from different years as contemporaneous; survey indicators are the least current.
