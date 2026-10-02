-- Equity gaps from the breakdowns kept in the core fact table. A gap pairs two groups from the
-- SAME survey or series (same source, upstream label and period), with every other dimension at
-- total, and with the indicator's defined age group. Comparing groups across surveys would
-- compare unlike things. Where several series offer a pair for one country-year-dimension, one
-- is chosen by source priority, then the latest period; the number of candidates is recorded.
INSERT INTO marts.equity_gap (
    run_id, country_key, indicator_key, year, dimension, group_a, group_b, value_a, value_b,
    ratio, difference, source, upstream_label, n_candidate_pairs)
WITH base AS (
    SELECT f.source_key, s.source_code, s.priority, f.country_key, f.indicator_key, f.year,
           f.upstream_label, f.period_text, f.sex, f.wealth_quintile, f.residence, f.value
    FROM core.fact_observation f
    JOIN core.dim_source s USING (source_key)
    JOIN core.dim_indicator i USING (indicator_key)
    WHERE f.run_id = %(run_id)s
      AND f.maternal_education = 'total'
      AND coalesce(f.other_dims ->> 'HEAD_OF_HOUSE', '_T') = '_T'
      AND coalesce(f.other_dims ->> 'AGE_AT_BIRTH', '_T') = '_T'
      AND (s.source_code <> 'unicef' OR f.age_group IS NOT DISTINCT FROM i.unicef_headline_age)
), wealth AS (
    SELECT 'wealth'::text AS dimension, 'poorest'::text AS group_a, 'richest'::text AS group_b,
           source_code, priority, country_key, indicator_key, year, upstream_label, period_text,
           max(value) FILTER (WHERE wealth_quintile = 'q1') AS va,
           max(value) FILTER (WHERE wealth_quintile = 'q5') AS vb
    FROM base
    WHERE sex = 'total' AND residence = 'total' AND wealth_quintile IN ('q1', 'q5')
    GROUP BY source_code, priority, country_key, indicator_key, year, upstream_label, period_text
    HAVING count(*) FILTER (WHERE wealth_quintile = 'q1') = 1
       AND count(*) FILTER (WHERE wealth_quintile = 'q5') = 1
), sex AS (
    SELECT 'sex'::text, 'female'::text, 'male'::text,
           source_code, priority, country_key, indicator_key, year, upstream_label, period_text,
           max(value) FILTER (WHERE sex = 'female'), max(value) FILTER (WHERE sex = 'male')
    FROM base
    WHERE wealth_quintile = 'total' AND residence = 'total' AND sex IN ('female', 'male')
    GROUP BY source_code, priority, country_key, indicator_key, year, upstream_label, period_text
    HAVING count(*) FILTER (WHERE sex = 'female') = 1 AND count(*) FILTER (WHERE sex = 'male') = 1
), residence AS (
    SELECT 'residence'::text, 'rural'::text, 'urban'::text,
           source_code, priority, country_key, indicator_key, year, upstream_label, period_text,
           max(value) FILTER (WHERE residence = 'rural'), max(value) FILTER (WHERE residence = 'urban')
    FROM base
    WHERE sex = 'total' AND wealth_quintile = 'total' AND residence IN ('rural', 'urban')
    GROUP BY source_code, priority, country_key, indicator_key, year, upstream_label, period_text
    HAVING count(*) FILTER (WHERE residence = 'rural') = 1 AND count(*) FILTER (WHERE residence = 'urban') = 1
), candidates AS (
    SELECT * FROM wealth UNION ALL SELECT * FROM sex UNION ALL SELECT * FROM residence
), chosen AS (
    SELECT c.*,
           row_number() OVER (PARTITION BY country_key, indicator_key, year, dimension
                              ORDER BY priority, period_text DESC NULLS LAST, upstream_label) AS rn,
           count(*) OVER (PARTITION BY country_key, indicator_key, year, dimension) AS n_pairs
    FROM candidates c
)
SELECT %(run_id)s, country_key, indicator_key, year, dimension, group_a, group_b, va, vb,
       va / nullif(vb, 0), va - vb, source_code, upstream_label, n_pairs
FROM chosen WHERE rn = 1
