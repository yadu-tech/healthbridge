-- Core layer: conformed dimensions and a fact table with an explicit, canonical grain.

CREATE TABLE core.dim_source (
    source_key  smallint PRIMARY KEY,
    source_code text     NOT NULL UNIQUE,
    name        text     NOT NULL,
    publisher   text     NOT NULL,
    base_url    text     NOT NULL,
    priority    smallint NOT NULL          -- tie-break order for reconciliation (1 = first)
);

CREATE TABLE core.dim_country (
    country_key     smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    iso3            char(3)  NOT NULL UNIQUE,
    iso2            char(2)  NOT NULL UNIQUE,
    name            text     NOT NULL,
    un_subregion    text     NOT NULL,     -- UN M49 geoscheme
    who_region_code text,                  -- as assigned by WHO in the staged data
    who_region_name text
);

CREATE TABLE core.country_alias (
    alias_norm text PRIMARY KEY,           -- lower-cased, accents and punctuation removed
    alias      text NOT NULL,
    iso3       char(3) NOT NULL REFERENCES core.dim_country (iso3),
    alias_type text NOT NULL
);

CREATE TABLE core.dim_indicator (
    indicator_key       smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    concept             text NOT NULL UNIQUE,
    name                text NOT NULL,
    unit                text NOT NULL,
    domain              text NOT NULL,
    valid_min           double precision NOT NULL,
    valid_max           double precision NOT NULL,
    unicef_headline_age text,              -- UNICEF AGE code defining the headline population; NULL = no age dimension
    definition          text NOT NULL
);

CREATE TABLE core.indicator_source_map (
    concept            text NOT NULL REFERENCES core.dim_indicator (concept),
    source_code        text NOT NULL REFERENCES core.dim_source (source_code),
    source_series_code text NOT NULL,
    PRIMARY KEY (concept, source_code)
);

CREATE TABLE core.dim_year (year integer PRIMARY KEY);

CREATE TABLE core.map_vocab (
    dimension    text NOT NULL,
    source_code  text NOT NULL REFERENCES core.dim_source (source_code),
    source_value text NOT NULL,
    canonical    text NOT NULL,
    note         text,
    PRIMARY KEY (dimension, source_code, source_value)
);

CREATE TABLE core.fact_observation (
    observation_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              text     NOT NULL,
    source_key          smallint NOT NULL REFERENCES core.dim_source,
    country_key         smallint NOT NULL REFERENCES core.dim_country,
    indicator_key       smallint NOT NULL REFERENCES core.dim_indicator,
    year                integer  NOT NULL REFERENCES core.dim_year,
    period_text         text,               -- period exactly as published (e.g. '2016-2017'); year is canonical
    value               double precision NOT NULL,
    lower_bound         double precision,
    upper_bound         double precision,
    sex                 text NOT NULL,
    wealth_quintile     text NOT NULL,
    residence           text NOT NULL,
    maternal_education  text NOT NULL,
    age_group           text,               -- source code; part of the concept definition for some indicators
    upstream_label      text,               -- e.g. survey or estimate series named by the source
    other_dims          jsonb,
    is_headline         boolean NOT NULL,   -- national total for the indicator's defined population
    is_selected         boolean NOT NULL,   -- the one row used per (source, country, indicator, year)
    n_candidates        integer NOT NULL,   -- headline rows competing for that cell
    selection_rule      text,
    quality_flags       text[]  NOT NULL DEFAULT '{}',
    staging_source_file text    NOT NULL,   -- lineage back to staging and the raw snapshot
    staging_row_num     integer NOT NULL
);
CREATE INDEX fact_observation_headline ON core.fact_observation (run_id, indicator_key, country_key, year)
    WHERE is_headline AND is_selected;

CREATE TABLE core.rejected_record (
    run_id      text    NOT NULL,
    source      text    NOT NULL,
    concept     text    NOT NULL,
    source_file text    NOT NULL,
    row_num     integer NOT NULL,
    reason      text    NOT NULL,
    detail      jsonb,
    PRIMARY KEY (run_id, source_file, row_num)
);

CREATE TABLE core.build_log (
    run_id           text PRIMARY KEY,
    built_at         timestamptz NOT NULL DEFAULT now(),
    staged_rows      integer NOT NULL,
    rows_loaded      integer NOT NULL,
    rows_rejected    integer NOT NULL,
    headline_rows    integer NOT NULL,
    selected_rows    integer NOT NULL,
    duration_seconds double precision,
    parameters       jsonb
);

-- Which sources are statistically dependent (publish the same underlying estimate), measured
-- on cells where both have a selected headline value. Dependence is derived, not asserted.
CREATE TABLE core.source_dependence (
    run_id           text NOT NULL,
    concept          text NOT NULL,
    source_a         text NOT NULL,
    source_b         text NOT NULL,
    shared_cells     integer NOT NULL,
    within_tolerance integer NOT NULL,
    share_within     double precision,
    median_rel_diff  double precision,
    tolerance        double precision NOT NULL,
    dependent        boolean NOT NULL,
    PRIMARY KEY (run_id, concept, source_a, source_b)
);

CREATE TABLE core.evidence_group (
    run_id      text NOT NULL,
    concept     text NOT NULL,
    source_code text NOT NULL,
    group_label text NOT NULL,
    PRIMARY KEY (run_id, concept, source_code)
);

CREATE TABLE core.fact_reconciled (
    run_id                 text     NOT NULL,
    country_key            smallint NOT NULL REFERENCES core.dim_country,
    indicator_key          smallint NOT NULL REFERENCES core.dim_indicator,
    year                   integer  NOT NULL,
    n_sources              integer  NOT NULL,
    n_evidence_groups      integer  NOT NULL,   -- 1 means the value cannot be cross-validated
    reconciled_value       double precision NOT NULL,
    reconciled_source_key  smallint NOT NULL REFERENCES core.dim_source,
    spread_abs             double precision,
    spread_rel             double precision,
    conflict               boolean  NOT NULL,
    group_values           jsonb    NOT NULL,
    PRIMARY KEY (run_id, country_key, indicator_key, year)
);

-- The default analyst surface: one selected national-total row per source, country, indicator, year.
CREATE VIEW core.v_headline_observation AS
SELECT f.run_id, s.source_code AS source, i.concept, c.iso3, f.year, f.value,
       f.lower_bound, f.upper_bound, f.period_text, f.upstream_label, f.n_candidates,
       f.observation_id, f.staging_source_file, f.staging_row_num
FROM core.fact_observation f
JOIN core.dim_source s USING (source_key)
JOIN core.dim_indicator i USING (indicator_key)
JOIN core.dim_country c USING (country_key)
WHERE f.is_headline AND f.is_selected;

-- Same shape as staging.v_observation so the data-quality engine can measure either layer.
CREATE VIEW core.v_observation_dq AS
SELECT run_id, source, concept, staging_source_file AS source_file, staging_row_num AS row_num,
       iso3, year::text AS period_text, year, value, value::text AS value_text,
       lower_bound AS low, upper_bound AS high, ''::text AS dim_key
FROM core.v_headline_observation;
