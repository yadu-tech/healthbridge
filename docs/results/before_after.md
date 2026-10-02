# Before vs after: staging layer vs core layer, snapshot `20261001T110703Z`

Both layers were measured with the same engine (dq runs 2 and 3). *Before* is the staged data as an analyst would first query it; *after* is the default analyst surface of the core layer: one selected national-total row per source, country, indicator and year (`core.v_headline_observation`).

## 1. Pipeline accounting

Nothing is dropped silently. Rows that are not loaded are listed with a reason in `core.rejected_record`; rows that are loaded but not on the default surface (sex, wealth-quintile, residence and age breakdowns, and non-selected surveys) stay in `core.fact_observation` with explicit dimensions.

| Source | Staged | Rejected | Reasons | Loaded | On default surface |
|---|---|---|---|---|---|
| unicef | 51,921 | 0 | none | 51,921 | 9,631 |
| who | 24,974 | 0 | none | 24,974 | 10,094 |
| worldbank | 13,608 | 3,478 | no_value: 3,478 | 10,130 | 10,130 |

## 2. Scores before and after

**Read with care.** After the pipeline, validity, uniqueness and consistency are 100% largely *by construction*: invalid rows are rejected and exactly one row is selected per cell. They show that the pipeline enforces these properties, not that the data improved in a way that was not enforced. Completeness rises mainly because the World Bank empty placeholder rows are rejected (section 4 shows coverage, which cleaning cannot improve).

| Scope | Dimension | Before | After |
|---|---|---|---|
| unicef | completeness | 100.0% | 100.0% |
| unicef | validity | 100.0% | 100.0% |
| unicef | uniqueness | 13.7% | 100.0% |
| unicef | consistency | 92.2% | 100.0% |
| unicef | composite | 76.5% | 100.0% |
| who | completeness | 100.0% | 100.0% |
| who | validity | 100.0% | 100.0% |
| who | uniqueness | 27.4% | 100.0% |
| who | consistency | 100.0% | 100.0% |
| who | composite | 81.9% | 100.0% |
| worldbank | completeness | 74.4% | 100.0% |
| worldbank | validity | 100.0% | 100.0% |
| worldbank | uniqueness | 100.0% | 100.0% |
| worldbank | consistency | 100.0% | 100.0% |
| worldbank | composite | 93.6% | 100.0% |
| all sources | completeness | 96.2% | 100.0% |
| all sources | validity | 100.0% | 100.0% |
| all sources | uniqueness | 30.5% | 100.0% |
| all sources | consistency | 95.5% | 100.0% |
| all sources | composite | 80.5% | 100.0% |

## 3. Cross-source join, before and after

*Fan-out* is joined rows per country-year (1.0x is a clean 1:1 join). *Conflicting* is the share of joined rows where WHO, World Bank and UNICEF differ by more than 10% of their mean. Before, much of that is breakdowns compared with totals; after, what remains is genuine disagreement between sources.

| Concept | Fan-out before | Fan-out after | Conflicting before | Conflicting after |
|---|---|---|---|---|
| dtp3_coverage | 1.0x | 1.0x | 1.0% | 1.0% |
| maternal_mortality_ratio | 1.0x | 1.0x | 3.1% | 3.1% |
| measles_mcv1_coverage | 1.0x | 1.0x | 1.2% | 1.2% |
| neonatal_mortality | 1.0x | 1.0x | 0.0% | 0.0% |
| skilled_birth_attendance | 6.3x | 1.0x | 59.0% | 0.0% |
| stunting_prevalence | 228.3x | 1.0x | 82.3% | 16.0% |
| under5_mortality | 57.9x | 1.0x | 65.3% | 0.0% |

## 4. Coverage on the default surface

Grid coverage is the share of the (54 countries x years) grid with a value. Cleaning cannot add data, so it should not rise; it can fall where a country-year exists only as a breakdown (for example only for adolescent women) and so has no headline row. Those rows remain in `core.fact_observation`.

| Source | Concept | Coverage before | Coverage after |
|---|---|---|---|
| unicef | dtp3_coverage | 95.1% | 95.1% |
| unicef | maternal_mortality_ratio | 64.9% | 64.9% |
| unicef | measles_mcv1_coverage | 95.1% | 95.1% |
| unicef | neonatal_mortality | 94.6% | 94.6% |
| unicef | skilled_birth_attendance | 17.6% | 17.4% |
| unicef | stunting_prevalence | 20.3% | 20.3% |
| unicef | under5_mortality | 94.6% | 94.6% |
| who | dtp3_coverage | 69.7% | 69.7% |
| who | maternal_mortality_ratio | 91.9% | 91.9% |
| who | measles_mcv1_coverage | 69.7% | 69.7% |
| who | neonatal_mortality | 94.6% | 94.6% |
| who | skilled_birth_attendance | 17.1% | 17.1% |
| who | stunting_prevalence | 67.6% | 67.6% |
| who | under5_mortality | 94.6% | 94.6% |
| worldbank | dtp3_coverage | 92.4% | 92.4% |
| worldbank | maternal_mortality_ratio | 91.9% | 91.9% |
| worldbank | measles_mcv1_coverage | 92.4% | 92.4% |
| worldbank | neonatal_mortality | 94.6% | 94.6% |
| worldbank | skilled_birth_attendance | 20.7% | 20.7% |
| worldbank | stunting_prevalence | 20.3% | 20.3% |
| worldbank | under5_mortality | 94.6% | 94.6% |

## 5. Source independence and reconciliation

Two sources are treated as *dependent* (publishing the same underlying estimate) when at least 90% of their shared country-years agree within 1%, with at least 30 shared country-years. Dependent sources form one *evidence group*; agreement inside a group is not independent confirmation. The reconciled value is never an average: it is the value of the highest-priority source of the highest-priority group (priority order WHO, UNICEF, World Bank; fixed and arbitrary). A conflict is flagged when independent groups differ by more than 10%; for those cells use `group_values`, not the single reconciled value.

| Concept | Evidence groups | Reconciled country-years | Cross-validated (2+ groups) | Conflicts among cross-validated |
|---|---|---|---|---|
| dtp3_coverage | unicef+who+worldbank | 1,901 | 0 (0.0%) | n/a |
| maternal_mortality_ratio | unicef+who+worldbank | 1,836 | 0 (0.0%) | n/a |
| measles_mcv1_coverage | unicef+who+worldbank | 1,901 | 0 (0.0%) | n/a |
| neonatal_mortality | unicef+who+worldbank | 1,890 | 0 (0.0%) | n/a |
| skilled_birth_attendance | unicef+who+worldbank | 451 | 0 (0.0%) | n/a |
| stunting_prevalence | unicef+worldbank, who | 1,430 | 326 (22.8%) | 51 of 326 (15.6%) |
| under5_mortality | unicef+who+worldbank | 1,890 | 0 (0.0%) | n/a |

Section 3 and this table measure conflict slightly differently: section 3 compares the values of all three sources, while this table compares one representative per evidence group, so the stunting figures differ a little (for example 16.0% vs 15.6%).

## 6. Limitations

- The comparison is for one snapshot and one set of judgements (plausible ranges, headline definitions, tolerances); all are recorded in `reference/` and `core.build_log`.
- Dependence is inferred from agreement of values, not from documentation, so two sources could agree by coincidence or differ for benign reasons such as rounding or estimate vintage.
- Section 2 measures properties the pipeline enforces; independent evidence of benefit comes from sections 1, 3, 4 and 5 and, later, the fault-injection experiment.
