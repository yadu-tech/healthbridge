-- Step 4: load accepted rows with an explicit grain. Rows are never deleted to resolve
-- ambiguity: they are flagged. A "headline" row is the national total for the indicator's
-- defined population; where a source still offers several headline rows for one country-year
-- (for example two surveys), exactly one is selected by a documented rule.
INSERT INTO core.fact_observation (
    run_id, source_key, country_key, indicator_key, year, period_text, value, lower_bound,
    upper_bound, sex, wealth_quintile, residence, maternal_education, age_group, upstream_label,
    other_dims, is_headline, is_selected, n_candidates, selection_rule, quality_flags,
    staging_source_file, staging_row_num)
SELECT %(run_id)s, ds.source_key, h.country_key, h.indicator_key, h.year, h.period_text, h.value,
       h.low, h.high, h.sex, h.wealth_quintile, h.residence, h.maternal_education, h.age_raw,
       h.upstream_label, h.other_dims, h.is_headline,
       (h.is_headline AND h.rk = 1) AS is_selected,
       h.n_candidates,
       CASE WHEN NOT h.is_headline THEN NULL
            WHEN h.n_candidates = 1 THEN 'single_candidate'
            ELSE 'source_priority_then_latest_period' END,
       array_remove(ARRAY[
           CASE WHEN h.low IS NOT NULL AND h.high IS NOT NULL
                     AND (h.value < h.low OR h.value > h.high) THEN 'bounds_violation' END,
           CASE WHEN h.period_text !~ '^[0-9]{4}$' THEN 'noncanonical_period' END
       ], NULL),
       h.source_file, h.row_num
FROM (
    SELECT k.*,
           count(*) FILTER (WHERE is_headline) OVER w AS n_candidates,
           row_number() OVER (w_ordered) AS rk
    FROM (
        SELECT c.*,
               (c.sex = 'total' AND c.wealth_quintile = 'total' AND c.residence = 'total'
                AND c.maternal_education = 'total'
                AND coalesce(c.other_dims ->> 'HEAD_OF_HOUSE', '_T') = '_T'
                AND coalesce(c.other_dims ->> 'AGE_AT_BIRTH', '_T') = '_T'
                -- UNICEF reports several age groups per indicator; keep the defined population
                AND (c.source <> 'unicef' OR c.age_raw IS NOT DISTINCT FROM c.unicef_headline_age)
               ) AS is_headline
        FROM core_classified c
        WHERE c.reject_reason IS NULL
    ) k
    WINDOW w AS (PARTITION BY source, country_key, indicator_key, year),
           w_ordered AS (PARTITION BY source, country_key, indicator_key, year
                         -- selection rule: headline first, then the producer's own priority flag
                         -- (UNICEF DATA_SOURCE_PRIORITY, higher wins), then the latest period,
                         -- then file order so the result is deterministic
                         ORDER BY CASE WHEN is_headline THEN 0 ELSE 1 END,
                                  priority DESC NULLS LAST, period_text DESC, source_file, row_num)
) h
JOIN core.dim_source ds ON ds.source_code = h.source
