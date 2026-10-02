-- Unweighted sub-regional summaries over the countries that have a value in that year. The set
-- of countries can change from year to year, so n_countries and coverage_share travel with every
-- row; compare years only where coverage is similar.
INSERT INTO marts.region_year (
    run_id, un_subregion, indicator_key, year, n_countries, n_possible, coverage_share,
    mean_value, median_value, min_value, max_value, p25_value, p75_value)
WITH members AS (
    SELECT un_subregion, count(*) AS n_possible FROM core.dim_country GROUP BY un_subregion
)
SELECT p.run_id, c.un_subregion, p.indicator_key, p.year, count(*), m.n_possible,
       count(*)::double precision / m.n_possible,
       avg(p.value),
       percentile_cont(0.5) WITHIN GROUP (ORDER BY p.value),
       min(p.value), max(p.value),
       percentile_cont(0.25) WITHIN GROUP (ORDER BY p.value),
       percentile_cont(0.75) WITHIN GROUP (ORDER BY p.value)
FROM marts.country_indicator_year p
JOIN core.dim_country c USING (country_key)
JOIN members m ON m.un_subregion = c.un_subregion
WHERE p.run_id = %(run_id)s
GROUP BY p.run_id, c.un_subregion, p.indicator_key, p.year, m.n_possible
