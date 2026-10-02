-- Spearman association between indicators across countries, on LEVELS. Each country contributes
-- its observation nearest to ref_year within +-tolerance years, so indicators published for
-- different years are not silently compared at far-apart dates. Conflicted values are left out.
-- Ties get average ranks. The interval is the approximate Fisher-z interval.
INSERT INTO marts.indicator_association (
    run_id, indicator_a, indicator_b, basis, reference, n_countries, spearman_rho, pearson_r,
    rho_ci_low, rho_ci_high)
WITH nearest AS (
    SELECT country_key, indicator_key, value,
           row_number() OVER (PARTITION BY country_key, indicator_key
                              ORDER BY abs(year - %(ref_year)s), year) AS rn
    FROM marts.country_indicator_year
    WHERE run_id = %(run_id)s AND abs(year - %(ref_year)s) <= %(tolerance)s
      AND quality_tier <> 'conflict'
), lvl AS (
    SELECT country_key, indicator_key, value FROM nearest WHERE rn = 1
), pairs AS (
    SELECT a.indicator_key AS ia, b.indicator_key AS ib, a.value AS va, b.value AS vb
    FROM lvl a JOIN lvl b ON a.country_key = b.country_key AND a.indicator_key < b.indicator_key
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
SELECT %(run_id)s, ia, ib, 'level', %(level_label)s, n, rho, r,
       CASE WHEN n > 3 AND abs(rho) < 1 THEN tanh(atanh(rho) - 1.96 / sqrt(n - 3)) END,
       CASE WHEN n > 3 AND abs(rho) < 1 THEN tanh(atanh(rho) + 1.96 / sqrt(n - 3)) END
FROM agg
