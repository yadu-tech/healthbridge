-- Trend over the window from trend_start. Requires enough points and a long enough span for a
-- slope to mean something. The log-linear rate only exists when every value is positive.
INSERT INTO marts.trend (
    run_id, country_key, indicator_key, window_start, window_end, n_points, value_start,
    value_end, absolute_change, linear_slope_per_year, linear_r2, annual_pct_change)
SELECT run_id, country_key, indicator_key, min(year), max(year), count(*),
       (array_agg(value ORDER BY year))[1],
       (array_agg(value ORDER BY year DESC))[1],
       (array_agg(value ORDER BY year DESC))[1] - (array_agg(value ORDER BY year))[1],
       regr_slope(value, year),
       regr_r2(value, year),
       CASE WHEN min(value) > 0
            THEN exp(regr_slope(ln(nullif(greatest(value, 0), 0)), year)) - 1 END
FROM marts.country_indicator_year
WHERE run_id = %(run_id)s AND year >= %(trend_start)s
GROUP BY run_id, country_key, indicator_key
HAVING count(*) >= %(trend_min_points)s AND max(year) - min(year) >= %(trend_min_span)s
