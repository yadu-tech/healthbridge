-- A common shape over the three staging tables, used by data-quality checks. It renames and
-- aligns columns only; it does not clean, filter or harmonize anything.
CREATE VIEW staging.v_observation AS
SELECT w.run_id, 'who'::text AS source, l.concept, w.source_file, w.row_num,
       w.spatial_dim AS iso3, w.time_dim::text AS period_text, w.time_dim AS year,
       w.numeric_value AS value, w.value_text, w.low, w.high,
       coalesce(w.dim1, '') || '|' || coalesce(w.dim2, '') || '|' || coalesce(w.dim3, '')
           || '|' || coalesce(w.data_source_dim, '') AS dim_key
FROM staging.who_observation w
JOIN staging.load_log l ON l.run_id = w.run_id AND l.source_file = w.source_file
UNION ALL
SELECT b.run_id, 'worldbank', l.concept, b.source_file, b.row_num,
       b.country_iso3, b.date_text, b.year,
       b.value, b.value::text, NULL::double precision, NULL::double precision, ''
FROM staging.worldbank_observation b
JOIN staging.load_log l ON l.run_id = b.run_id AND l.source_file = b.source_file
UNION ALL
SELECT u.run_id, 'unicef', l.concept, u.source_file, u.row_num,
       u.ref_area, u.time_period, u.year,
       u.obs_value, u.value_text, u.lower_bound, u.upper_bound,
       coalesce(u.sex, '') || '|' || coalesce(u.age, '') || '|' || coalesce(u.wealth_quintile, '')
           || '|' || coalesce(u.residence, '') || '|' || coalesce(u.data_source, '')
           || '|' || coalesce(u.extra::text, '')
FROM staging.unicef_observation u
JOIN staging.load_log l ON l.run_id = u.run_id AND l.source_file = u.source_file;

-- One row per data-quality run; metrics are stored long-form so new checks need no schema change.
CREATE TABLE dq.run (
    dq_run_id        bigserial PRIMARY KEY,
    snapshot_run_id  text             NOT NULL,
    stage            text             NOT NULL,   -- 'staging' now; later 'core' for the "after" state
    computed_at      timestamptz      NOT NULL DEFAULT now(),
    duration_seconds double precision,
    details          jsonb
);

CREATE TABLE dq.metric (
    dq_run_id   bigint NOT NULL REFERENCES dq.run (dq_run_id) ON DELETE CASCADE,
    scope       text   NOT NULL,                  -- series | source | concept | overall
    source      text   NOT NULL DEFAULT '',
    concept     text   NOT NULL DEFAULT '',
    dimension   text   NOT NULL,                  -- completeness | validity | uniqueness | consistency | integration | score
    metric      text   NOT NULL,
    value       double precision,
    numerator   bigint,
    denominator bigint,
    details     jsonb,
    PRIMARY KEY (dq_run_id, scope, source, concept, dimension, metric)
);
