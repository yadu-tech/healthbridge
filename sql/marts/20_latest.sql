-- Latest value per country and indicator. years_behind says how stale it is relative to the
-- most recent year available for that indicator anywhere, so old survey values are not
-- mistaken for current ones.
INSERT INTO marts.country_latest (
    run_id, country_key, indicator_key, latest_year, value, quality_tier, first_year,
    n_observations, years_behind)
WITH ranked AS (
    SELECT p.*,
           row_number() OVER (PARTITION BY country_key, indicator_key ORDER BY year DESC) AS rn,
           count(*) OVER (PARTITION BY country_key, indicator_key) AS n_obs,
           min(year) OVER (PARTITION BY country_key, indicator_key) AS first_year,
           max(year) OVER (PARTITION BY indicator_key) AS latest_anywhere
    FROM marts.country_indicator_year p
    WHERE p.run_id = %(run_id)s
)
SELECT run_id, country_key, indicator_key, year, value, quality_tier, first_year, n_obs,
       latest_anywhere - year
FROM ranked WHERE rn = 1
