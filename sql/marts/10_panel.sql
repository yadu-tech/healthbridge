-- The panel: one reconciled value per (country, indicator, year) with its trust information.
-- The outlier flag is read from the fact row of the source whose value was reconciled.
INSERT INTO marts.country_indicator_year (
    run_id, country_key, indicator_key, year, value, reconciled_source, n_sources,
    n_evidence_groups, quality_tier, outlier_flag, source_disagreement)
SELECT r.run_id, r.country_key, r.indicator_key, r.year, r.reconciled_value, s.source_code,
       r.n_sources, r.n_evidence_groups,
       CASE WHEN r.conflict THEN 'conflict'
            WHEN r.n_evidence_groups > 1 THEN 'cross_validated'
            ELSE 'single_evidence_group' END,
       coalesce('temporal_outlier' = ANY(f.quality_flags), false),
       r.within_group_disagreement
FROM core.fact_reconciled r
JOIN core.dim_source s ON s.source_key = r.reconciled_source_key
LEFT JOIN core.fact_observation f
       ON f.run_id = r.run_id AND f.country_key = r.country_key
      AND f.indicator_key = r.indicator_key AND f.year = r.year
      AND f.source_key = r.reconciled_source_key AND f.is_headline AND f.is_selected
WHERE r.run_id = %(run_id)s
