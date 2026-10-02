-- Spearman association between indicators across countries, on CHANGES: the difference between
-- a country's observation near ref_year and its observation near base_year (each nearest within
-- +-tolerance years), required to be at least change_min_span years apart. Rank correlation, so
-- indicators in different units can be compared. Associations only, never cause.
INSERT INTO marts.indicator_association (
    run_id, indicator_a, indicator_b, basis, reference, n_countries, spearman_rho, pearson_r,
    rho_ci_low, rho_ci_high)
WITH near_end AS (
    SELECT country_key, indicator_key, value, year,
           row_number() OVER (PARTITION BY country_key, indicator_key
                              ORDER BY abs(year - %(ref_year)s), year) AS rn
    FROM marts.country_indicator_year
    WHERE run_id = %(run_id)s AND abs(year - %(ref_year)s) <= %(tolerance)s
      AND quality_tier <> 'conflict'
), near_start AS (
    SELECT country_key, indicator_key, value, year,
           row_number() OVER (PARTITION BY country_key, indicator_key
                              ORDER BY abs(year - %(base_year)s), year) AS rn
    FROM marts.country_indicator_year
    WHERE run_id = %(run_id)s AND abs(year - %(base_year)s) <= %(tolerance)s
      AND quality_tier <> 'conflict'
), change AS (
    SELECT e.country_key, e.indicator_key, e.value - s.value AS delta
    FROM near_end e JOIN near_start s USING (country_key, indicator_key)
    WHERE e.rn = 1 AND s.rn = 1 AND e.year - s.year >= %(change_min_span)s
), pairs AS (
    SELECT a.indicator_key AS ia, b.indicator_key AS ib, a.delta AS va, b.delta AS vb
    FROM change a JOIN change b ON a.country_key = b.country_key AND a.indicator_key < b.indicator_key
), ranked AS (
    SELECT ia, ib, va, vb,
           rank() OVER (PARTITION BY ia, ib ORDER BY va)
             + (count(*) OVER (PARTITION BY ia, ib, va) - 1) / 2.0 AS ra,
           rank() OVER (PARTITION BY ia, ib ORDER BY vb)
             + (count(*) OVER (PARTITION BY ia, ib, vb) - 1) / 2.0 AS rb
    FROM pairs
), agg AS (
    SELECT ia, ib, count(*) AS n, corr(ra, rb) AS rho, corr(va, vb) AS r
    FROM ranked GROUP BY ia, ib
    HAVING count(*) >= %(min_countries)s AND corr(ra, rb) IS NOT NULL
)
SELECT %(run_id)s, ia, ib, 'change', %(change_label)s, n, rho, r,
       CASE WHEN n > 3 AND abs(rho) < 1 THEN tanh(atanh(rho) - 1.96 / sqrt(n - 3)) END,
       CASE WHEN n > 3 AND abs(rho) < 1 THEN tanh(atanh(rho) + 1.96 / sqrt(n - 3)) END
FROM agg
