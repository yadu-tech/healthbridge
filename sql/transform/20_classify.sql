-- Step 2: resolve keys and canonical vocabulary, then decide for every row whether it is kept
-- or rejected and why. A row gets at most one reason; the first matching rule wins.
CREATE TEMP TABLE core_classified AS
WITH resolved AS (
    SELECT s.*,
           -- country identification: exact ISO3 first, then documented fallbacks, so that a
           -- recoverable identifier (case, padding, ISO2, a known name) is repaired and recorded
           coalesce(c1.country_key, c2.country_key, c3.country_key, c4.country_key) AS country_key,
           CASE WHEN c1.country_key IS NOT NULL THEN 'exact'
                WHEN c2.country_key IS NOT NULL THEN 'normalized'
                WHEN c3.country_key IS NOT NULL THEN 'iso2'
                WHEN c4.country_key IS NOT NULL THEN 'alias' END AS country_resolution,
           i.indicator_key, i.valid_min, i.valid_max, i.unicef_headline_age,
           -- an absent dimension means "total" (e.g. World Bank rows carry no sex)
           CASE WHEN s.sex_raw IS NULL THEN 'total' ELSE sx.canonical END AS sex,
           CASE WHEN s.wealth_raw IS NULL THEN 'total' ELSE wl.canonical END AS wealth_quintile,
           CASE WHEN s.residence_raw IS NULL THEN 'total' ELSE rs.canonical END AS residence,
           CASE WHEN s.maternal_edu_raw IS NULL OR s.maternal_edu_raw = '_T' THEN 'total'
                ELSE s.maternal_edu_raw END AS maternal_education
    FROM core_std s
    LEFT JOIN core.dim_country c1 ON c1.iso3 = s.iso3
    LEFT JOIN core.dim_country c2 ON c1.country_key IS NULL AND c2.iso3 = upper(btrim(s.iso3))
    LEFT JOIN core.dim_country c3 ON c1.country_key IS NULL AND c2.country_key IS NULL
                                  AND c3.iso2 = upper(btrim(s.iso3))
    LEFT JOIN core.country_alias al ON c1.country_key IS NULL AND c2.country_key IS NULL
                                    AND c3.country_key IS NULL
                                    AND al.alias_norm = core.normalize_name(s.iso3)
    LEFT JOIN core.dim_country c4 ON c4.iso3 = al.iso3
    LEFT JOIN core.dim_indicator i ON i.concept = s.concept
    LEFT JOIN core.map_vocab sx ON sx.dimension = 'sex' AND sx.source_code = s.source
                                AND sx.source_value = s.sex_raw
    LEFT JOIN core.map_vocab wl ON wl.dimension = 'wealth_quintile' AND wl.source_code = s.source
                                AND wl.source_value = s.wealth_raw
    LEFT JOIN core.map_vocab rs ON rs.dimension = 'residence' AND rs.source_code = s.source
                                AND rs.source_value = s.residence_raw
), reasoned AS (
    SELECT r.*,
        CASE
            WHEN country_key IS NULL THEN 'unknown_country'
            WHEN year IS NULL OR year < %(year_min)s OR year > %(year_max)s THEN 'invalid_year'
            WHEN value IS NULL AND value_text IS NOT NULL THEN 'unparseable_value'
            WHEN value IS NULL THEN 'no_value'
            WHEN value < valid_min OR value > valid_max THEN 'out_of_range'
            WHEN unexpected_dim OR sex IS NULL OR wealth_quintile IS NULL OR residence IS NULL
                THEN 'unmapped_vocabulary'
        END AS reason
    FROM resolved r
)
SELECT q.*,
       CASE WHEN reason IS NULL AND dup_rank > 1 THEN 'exact_duplicate' ELSE reason END AS reject_reason
FROM (
    SELECT t.*,
           row_number() OVER (
               PARTITION BY source, concept, country_key, year, period_text, sex, wealth_quintile,
                            residence, maternal_education, age_raw, upstream_label, value,
                            other_dims::text
               ORDER BY source_file, row_num) AS dup_rank
    FROM reasoned t
    WHERE reason IS NULL
    UNION ALL
    SELECT t.*, 1 AS dup_rank FROM reasoned t WHERE reason IS NOT NULL
) q
