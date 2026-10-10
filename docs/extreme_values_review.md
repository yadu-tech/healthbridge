# Review of extreme under-5 mortality values

_Reviewed 2026-10-10 against snapshot `20261001T110703Z`. Hand-written record, not generated._

The analytics report listed under-5 mortality values that rise sharply (Central African Republic, South Sudan, Libya, Somalia) and said they had not been checked against the producer's documentation. This is that check. **Short answer: none of them is an artefact of this pipeline, and most are what the producer's method is documented to produce for crisis periods. They are estimates of crisis mortality, not measurements, and they remain unconfirmed against the producer's country-level notes, which I could not read.**

## What was checked, and how

All three sources (WHO, UNICEF, World Bank) republish one upstream estimate, the UN Inter-agency Group for Child Mortality Estimation (UN IGME) series, so their agreement says nothing about correctness. The question is what the producer says about these values.

| Source | Type | What it supports |
|---|---|---|
| Alkema and New (2014), [*Global estimation of child mortality using a Bayesian B-spline bias-reduction model*](https://arxiv.org/abs/1309.1602), Annals of Applied Statistics 8(4) | Primary (the method paper behind UN IGME's 2013 update). Read in full text. | Countries with conflict or natural disasters use a modified method. For Somalia and DR Congo, conflict periods are fitted with a single coefficient, so "the resulting fit is constant during the conflict periods". Crisis-related mortality is "not included in the B3 method", that is, it is handled outside the model. |
| [Our World in Data, infant mortality indicator page](https://archive.ourworldindata.org/20260904-080400/grapher/infant-mortality.html) (archived 2026-09-04) | Secondary. The fetch tool summarised the page; I did not read the primary UN IGME note. | In countries hit by a humanitarian crisis UN IGME "fits its trend with the crisis years set aside and then adds the crisis deaths back", giving one-year spikes: Rwanda 1994, Somalia 2011, Central African Republic 2009, 2019 and 2022. |
| UN World Population Prospects 2024 series for South Sudan, as copied by [Measured World](https://measuredworld.com/countries/south-sudan/mortality/child-survival) | Third-party copy of an independent UN series. | 2015 to 2019: 244.7, 272.7, 287.4, 142.7, 99.4, then roughly 99 to 98 through 2024. |

Not obtainable: UNICEF's report pages and PDFs, the UN IGME portal and the LSHTM news page returned HTTP 403 or no content to automated fetching, so UN IGME's own country notes were not read. The method paper describes the 2013 vintage; later vintages may differ.

## Findings by value

The 12 under-5 mortality values flagged by the temporal outlier check, plus the two long constant runs found while reviewing them.

| Value | Series | What the sources support | Status |
|---|---|---|---|
| Rwanda 1994 | 389.0 | Named as a documented crisis spike (OWID). | Documented (secondary) |
| Somalia 2011 | 363.1 | Named as a documented crisis spike (OWID). | Documented (secondary) |
| Central African Republic 2009, 2019, 2022 | 489.3, 254.7, 424.6 | All three named as documented crisis spikes (OWID). | Documented (secondary) |
| Somalia 1995 to 2004 | constant 173.2 for ten years | The method paper says the fit is constant during conflict periods for Somalia. It does not say which years. | Explained by the documented method; the exact years are not confirmed |
| South Sudan 2014 to 2017 | 182.6, 240.8, 272.9, 292.0 (then 133.7 in 2018) | Not a one-year spike. An independent UN series (WPP 2024) has the same shape and nearly the same values, so this is not a loading or reconciliation artefact. Whether the size is right is the producers' judgement. | Reproduced by an independent series; not documented in the sources read |
| South Sudan 2019 to 2024 | constant 96.7 for six years | WPP 2024 is near-flat at about 99 to 98 over the same years. No documentation found for South Sudan. | Unexplained; resembles a constant fit |
| Central African Republic 2014 | 186.8 | Not named in the sources read. Falls in the period of the country's 2013 to 2014 conflict (general knowledge, not from these sources). | Plausible, not documented |
| Somalia 2022 | 143.1 | Not named in the sources read. | Plausible (drought year, general knowledge), not documented |
| Libya 2023 | 31.7 (neighbours about 10) | Not named in the sources read. Coincides with the September 2023 Derna floods, for which reported death tolls conflict (roughly 3,000 to 11,300). I found no statement that UN IGME adds flood deaths, and I did not check whether the size is consistent with the reported toll. | Plausible, not documented |
| Ethiopia 2021 and 2023 | 70.4 and 51.5 | Not examined beyond the flag. | Not reviewed |
| Sudan 1998, South Sudan 1998 | 148.3 and 430.8 | Not examined beyond the flag. The South Sudan value is very high. | Not reviewed |

One lead I could not confirm: a search summary of an LSHTM study of South Sudan (Checchi et al., 2018) said under-five death rates were no higher during the war than before it. The study's ALNAP summary does not say this, and I could not open the LSHTM page, so this is **not** used as evidence either way. If true, it would contradict the 2014 to 2017 rise and is worth reading in full.

## What this changes

1. **A flag is not an error.** The temporal outlier check fires on estimates that the producer deliberately adds for crisis years. For under-5 mortality, at least 5 of the 12 flagged values are named as documented crisis estimates. Treat a flag as "this value is unusual and needs a reason", not "this value is wrong". The pipeline keeps flagged rows, as before.
2. **The ablation result for under-5 mortality is better explained.** Variant (a) and (c) drop flagged values, so for this indicator they drop genuine crisis-year estimates as well as corruptions. That is consistent with the pipeline showing no benefit for under-5 mortality, and with the naive variant at 1% scoring better than the clean reference. This supports the explanation recorded in the ablation outcome; it is still not a controlled test of it.
3. **Flat stretches in a modelled series are model features, not data** (now flagged by the pipeline, see the follow-up below). Somalia (ten identical values) and South Sudan (six) show that a run of identical values in a mortality series reflects a modelling choice. Trend estimates for these countries (annual percentage change since 2000) are affected and should be read with that in mind. Repeated integers in coverage indicators are normal and are not the same phenomenon.
4. **Analyses that use these series should say what they are.** They are estimates that include modelled crisis deaths, not observed mortality, and the forecasting study in particular extrapolates them (see the report's limitations).

## Still open

- Read UN IGME's own country notes for the values marked "not documented" (from childmortality.org, which blocked automated access) and confirm the years of Somalia's constant fit.
- Find whether South Sudan's constant 96.7 is a documented projection.
- Read the LSHTM study for what it says about child mortality.

## Follow-up: the flat-run check

After this review the pipeline gained a `flat_run` flag ([harmonization.md](harmonization.md), D9). It labels the two constant runs found here and nothing else on the first snapshot (16 country-years, in WHO and UNICEF rows). It is informational: it does not remove values. A correction to this review: it said no constant run of three or more years existed in neonatal or maternal mortality. That was true of the reconciled panel but not of every source's rows: the World Bank's one-decimal values repeat through rounding in slowly changing low-mortality series (for example Seychelles neonatal mortality at 8.7 for twelve years). The check therefore does not assess series published to one decimal or less.
