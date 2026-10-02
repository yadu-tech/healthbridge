-- Step 1: put the three staging tables into one shape. Source-specific dimension columns are
-- mapped onto common *_raw columns; nothing is interpreted yet.
CREATE TEMP TABLE core_std AS
SELECT 'who'::text AS source, l.concept, w.source_file, w.row_num,
       w.spatial_dim AS iso3, w.time_dim AS year, w.time_dim::text AS period_text,
       w.numeric_value AS value, w.value_text, w.low, w.high,
       CASE WHEN w.dim1_type = 'SEX' THEN w.dim1 END AS sex_raw,
       CASE WHEN w.dim3_type = 'WEALTHQUINTILE' THEN w.dim3 END AS wealth_raw,
       NULL::text AS residence_raw,
       NULL::text AS maternal_edu_raw,
       w.dim2 AS age_raw,
       w.data_source_dim AS upstream_label,
       NULL::int AS priority,
       NULL::jsonb AS other_dims,
       -- a dimension type we have no rule for must not be silently ignored
       (w.dim1_type IS DISTINCT FROM 'SEX' AND w.dim1 IS NOT NULL)
         OR (w.dim2_type IS DISTINCT FROM 'AGEGROUP' AND w.dim2 IS NOT NULL)
         OR (w.dim3_type IS DISTINCT FROM 'WEALTHQUINTILE' AND w.dim3 IS NOT NULL) AS unexpected_dim
FROM staging.who_observation w
JOIN staging.load_log l ON l.run_id = w.run_id AND l.source_file = w.source_file
WHERE w.run_id = %(run_id)s
UNION ALL
SELECT 'worldbank', l.concept, b.source_file, b.row_num,
       b.country_iso3, b.year, b.date_text,
       b.value, b.value::text, NULL::double precision, NULL::double precision,
       NULL, NULL, NULL, NULL, NULL, NULL, NULL::int, NULL::jsonb, false
FROM staging.worldbank_observation b
JOIN staging.load_log l ON l.run_id = b.run_id AND l.source_file = b.source_file
WHERE b.run_id = %(run_id)s
UNION ALL
SELECT 'unicef', l.concept, u.source_file, u.row_num,
       u.ref_area, u.year, u.time_period,
       u.obs_value, u.value_text, u.lower_bound, u.upper_bound,
       u.sex, u.wealth_quintile, u.residence,
       coalesce(u.extra ->> 'MATERNAL_EDU_LVL', u.extra ->> 'MOTHER_EDUCATION'),
       u.age, u.data_source,
       (u.extra ->> 'DATA_SOURCE_PRIORITY')::int, u.extra, false
FROM staging.unicef_observation u
JOIN staging.load_log l ON l.run_id = u.run_id AND l.source_file = u.source_file
WHERE u.run_id = %(run_id)s
