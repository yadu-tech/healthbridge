-- How far each indicator can be trusted: coverage, and the mix of quality tiers.
INSERT INTO marts.data_trust (
    run_id, indicator_key, n_values, n_countries, year_min, year_max, grid_coverage,
    share_cross_validated, share_single_evidence, share_conflict, share_outlier_flag,
    share_source_disagreement)
SELECT run_id, indicator_key, count(*), count(DISTINCT country_key), min(year), max(year),
       count(*)::double precision / (%(n_countries)s * (%(year_max)s - %(year_min)s + 1)),
       avg((quality_tier = 'cross_validated')::int),
       avg((quality_tier = 'single_evidence_group')::int),
       avg((quality_tier = 'conflict')::int),
       avg(outlier_flag::int),
       avg(source_disagreement::int)
FROM marts.country_indicator_year
WHERE run_id = %(run_id)s
GROUP BY run_id, indicator_key
