-- Step 6: one reconciled value per (country, indicator, year). No averaging: sources that publish
-- the same underlying estimate form one evidence group, and the value of the highest-priority
-- source of the highest-priority group is used. A conflict is flagged when independent evidence
-- groups differ by more than the tolerance.
INSERT INTO core.fact_reconciled (
    run_id, country_key, indicator_key, year, n_sources, n_evidence_groups, reconciled_value,
    reconciled_source_key, spread_abs, spread_rel, conflict, group_values,
    max_within_group_spread_rel, within_group_disagreement)
WITH cand AS (
    SELECT f.country_key, f.indicator_key, f.year, f.source_key, s.priority, f.value, g.group_label
    FROM core.fact_observation f
    JOIN core.dim_source s USING (source_key)
    JOIN core.dim_indicator i USING (indicator_key)
    JOIN core.evidence_group g ON g.run_id = f.run_id AND g.concept = i.concept
                              AND g.source_code = s.source_code
    WHERE f.run_id = %(run_id)s AND f.is_headline AND f.is_selected
), rep AS (
    SELECT DISTINCT ON (country_key, indicator_key, year, group_label)
           country_key, indicator_key, year, group_label, source_key, priority, value
    FROM cand
    ORDER BY country_key, indicator_key, year, group_label, priority
), within_group AS (
    -- sources of one evidence group normally publish the same number; a large gap between them
    -- in a single cell is a data-source inconsistency worth flagging
    SELECT country_key, indicator_key, year,
           max(CASE WHEN n > 1 AND mean_value <> 0 THEN (hi - lo) / abs(mean_value) ELSE 0 END) AS max_spread
    FROM (SELECT country_key, indicator_key, year, group_label, count(*) AS n,
                 max(value) AS hi, min(value) AS lo, avg(value) AS mean_value
          FROM cand GROUP BY 1, 2, 3, 4) g
    GROUP BY country_key, indicator_key, year
), cell AS (
    SELECT country_key, indicator_key, year,
           count(*) AS n_groups,
           (array_agg(value ORDER BY priority))[1] AS reconciled_value,
           (array_agg(source_key ORDER BY priority))[1] AS reconciled_source_key,
           max(value) - min(value) AS spread_abs,
           avg(value) AS mean_value,
           jsonb_object_agg(group_label, value) AS group_values
    FROM rep GROUP BY country_key, indicator_key, year
)
SELECT %(run_id)s, c.country_key, c.indicator_key, c.year,
       (SELECT count(*) FROM cand x WHERE x.country_key = c.country_key
           AND x.indicator_key = c.indicator_key AND x.year = c.year),
       c.n_groups, c.reconciled_value, c.reconciled_source_key,
       CASE WHEN c.n_groups > 1 THEN c.spread_abs END,
       CASE WHEN c.n_groups > 1 AND c.mean_value <> 0 THEN c.spread_abs / abs(c.mean_value) END,
       (c.n_groups > 1 AND c.mean_value <> 0
            AND c.spread_abs / abs(c.mean_value) > %(conflict_tolerance)s),
       c.group_values,
       w.max_spread,
       w.max_spread > %(group_disagreement_tolerance)s
FROM cell c
JOIN within_group w USING (country_key, indicator_key, year)
