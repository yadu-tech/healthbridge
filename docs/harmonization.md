# Harmonization decisions

How the core layer turns three differently-shaped sources into one consistent model. Each decision records the rule, the evidence from the data, the alternatives that were considered, and the consequence for users. Judgements that could reasonably differ are marked **judgement** and are stored as data or parameters so they can be changed and re-run.

Build: `python -m healthbridge.core build`. Transformations are plain SQL in [`sql/transform/`](../sql/transform); reference data is in [`reference/`](../reference).

## D1. Make the grain explicit instead of flattening it

**Decision.** `core.fact_observation` keeps every accepted row and gives it explicit, canonical dimensions: sex, wealth quintile, residence, maternal education, age group, and the upstream series label. Nothing is deleted to make rows unique; rows are flagged instead (`is_headline`, `is_selected`).

**Evidence.** After staging, only 30.5% of rows were unique at the (country, indicator, year) grain. The sources mix national totals with sex, wealth-quintile, residence and survey breakdowns under the same country and year. A naive join multiplied under-5 mortality rows 58x and stunting rows 228x.

**Alternatives.** (a) Keep only national totals: simpler, but discards equity information (for example under-5 mortality by wealth quintile). (b) One wide table per breakdown: fragments the model.

**Consequence.** Analysts query `core.v_headline_observation` (one row per source, country, indicator, year) by default and reach breakdowns through the fact table when they want them.

## D2. What counts as the headline row

A row is **headline** when sex, wealth quintile, residence and maternal education are all `total`, any head-of-household or age-at-birth dimension is `_T`, and, for UNICEF, the age code equals the indicator's defined population:

| Concept | UNICEF headline age | Why |
|---|---|---|
| under-5 mortality, neonatal mortality | none | age is inherent to the indicator |
| maternal mortality ratio | `_T` | total |
| skilled birth attendance | `Y15T49` | women 15-49; `Y15T19` (adolescents) is a different population |
| DTP3, MCV1 coverage | `M12T23` | children aged 12-23 months |
| stunting | `_T` | total under 5; 20+ age sub-bands exist |

**Evidence.** Observed distinct age codes per indicator in `staging.unicef_observation`. WHO and World Bank series have no age dimension to choose between (WHO's age group is fixed per indicator). **Judgement:** the table above lives in `reference/indicators.csv`.

A dimension absent from a source means `total` (World Bank rows carry no sex, for instance).

## D3. One vocabulary

`reference/vocabulary.csv` maps each source's codes to canonical values. UNICEF wealth codes were checked against UNICEF's official code list (`CL_WEALTH_QUINTILE`), not guessed (`B20` = bottom 20%, `R20` = richest 20%, `M40` = middle 40%). WHO's quintile orientation (`WQ1` = poorest) is inferred from the observed gradient (highest under-5 mortality in `WQ1`) and flagged in the file for verification against WHO metadata.

**An unmapped code is rejected, not guessed.** A row whose sex, wealth or residence code is not in the crosswalk (or whose WHO dimension type is unexpected) goes to `core.rejected_record` as `unmapped_vocabulary`. A new code appearing in a source therefore surfaces as a measurable rejection, not as silently wrong data.

## D4. Selecting one row when a source still offers several

**Evidence.** After applying D2, one ambiguity remains: UNICEF stunting has 436 headline rows for 406 country-years, because several surveys can cover the same year. UNICEF flags a primary survey with `DATA_SOURCE_PRIORITY` (1 = primary). The surveys can differ materially (for example Malawi 2019: 34.0 vs 40.9).

**Decision.** Among headline rows for one (source, country, indicator, year): the producer's own priority flag wins (higher first), then the later period, then file order so the result is deterministic. The row is marked `is_selected`; the others stay in the fact table with `n_candidates` and `selection_rule` recording that a choice was made.

**Alternatives.** Average the surveys (invents a number no survey reported), or take the latest (ignores the producer's flag). **Judgement.**

## D5. Rejections have reasons

A row is rejected, with one reason (first match wins), when: `unknown_country`, `invalid_year` (outside the snapshot scope), `unparseable_value`, `no_value`, `out_of_range` (outside the plausible range in `reference/indicators.csv`), `unmapped_vocabulary`, or `exact_duplicate` (identical in every attribute). On the first real snapshot the only rejections were 3,478 World Bank placeholder rows with no value.

A value outside the source's *own* uncertainty bounds is kept and flagged (`bounds_violation`), because the value itself may be usable. A non-year period such as `2016-2017` is kept with the original text in `period_text` and the canonical `year` used for analysis.

## D6. Countries

Keys are ISO 3166-1 alpha-3 codes. `reference/countries.csv` lists the 54 states with ISO2 codes, canonical names, and the UN M49 sub-region. At build time the reference is cross-checked against the data: ISO2 codes must match the World Bank's, and every country must have a single WHO region. A mismatch stops the build (`ReferenceMismatch`).

WHO's region for each country is stored beside the UN sub-region because they differ: 7 of the 54 states (Djibouti, Egypt, Libya, Morocco, Sudan, Somalia, Tunisia) are in WHO's `EMR`, not `AFR`. Neither is "the" definition of Africa; both are kept so the choice is explicit. `core.country_alias` resolves name variants (for example "Ivory Coast", "Swaziland", "Gambia, The") to ISO3 and is used by the evaluation of name-based joining.

## D7. Sources are not independent: derive it, do not assert it

**Evidence.** For six of seven concepts, at least 93% of shared country-years agree within 1% between every pair of sources, with median relative differences of 0.4% or less. They republish the same upstream estimates. Stunting is different: WHO's model-based estimates agree with UNICEF's survey-based values in only about 11% of country-years, while World Bank and UNICEF agree in 99%.

**Decision.** Two sources are *dependent* for a concept when at least 30 shared headline country-years exist and at least 90% agree within 1%. Dependence is transitive: dependent sources form one **evidence group**, computed per concept and stored in `core.source_dependence` and `core.evidence_group`. Agreement inside a group is not independent confirmation.

**Judgement.** The tolerance (1%), minimum evidence (30) and agreement share (90%) are parameters of `build_core`, recorded in `core.build_log.parameters`, so their effect can be tested.

## D8. Reconciliation without invention

`core.fact_reconciled` has one row per (country, indicator, year). The reconciled value is the value of the highest-priority source in the highest-priority evidence group. It is never an average. Source priority (WHO, UNICEF, World Bank) is fixed and arbitrary; where sources are dependent the choice does not change the value.

`n_evidence_groups = 1` means the value **cannot be cross-validated**. When independent groups differ by more than 10% (**judgement**) the cell is flagged `conflict` and consumers should read `group_values`, not the single reconciled value. On the first snapshot, 51 of 326 cross-validated stunting country-years were conflicts (15.6%), and no other concept had cross-validated cells.

## What this layer does not do

- It does not decide which source is *right*; it exposes where independent evidence disagrees.
- Outlier detection is not applied; it belongs after harmonization and is future work.
- Subnational data, World Bank region and income group, and AU regions are not included.
