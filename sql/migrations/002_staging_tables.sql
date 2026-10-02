-- Staging: typed but NOT cleaned or harmonized. One table per source, keeping the source's own
-- vocabulary. Every row carries lineage (run_id, source_file, row_num) back to the raw snapshot.
-- Raw value text is kept next to the parsed number so unparseable values remain countable.

CREATE TABLE staging.load_log (
    run_id       text        NOT NULL,
    source       text        NOT NULL,
    series       text        NOT NULL,
    concept      text        NOT NULL,
    source_file  text        NOT NULL,
    sha256       text        NOT NULL,
    rows_loaded  integer     NOT NULL,
    loaded_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, source_file)
);

CREATE TABLE staging.who_observation (
    run_id               text    NOT NULL,
    source_file          text    NOT NULL,
    row_num              integer NOT NULL,
    who_id               bigint,
    indicator_code       text,
    spatial_dim_type     text,
    spatial_dim          text,
    parent_location_code text,
    parent_location      text,
    time_dim_type        text,
    time_dim             integer,
    dim1_type            text,
    dim1                 text,
    dim2_type            text,
    dim2                 text,
    dim3_type            text,
    dim3                 text,
    data_source_dim_type text,
    data_source_dim      text,
    value_text           text,
    numeric_value        double precision,
    low                  double precision,
    high                 double precision,
    comments             text,
    source_modified_at   timestamptz,
    PRIMARY KEY (run_id, source_file, row_num)
);
CREATE INDEX who_observation_key ON staging.who_observation (run_id, indicator_code, spatial_dim, time_dim);

CREATE TABLE staging.worldbank_observation (
    run_id         text    NOT NULL,
    source_file    text    NOT NULL,
    row_num        integer NOT NULL,
    indicator_id   text,
    indicator_name text,
    country_id     text,
    country_name   text,
    country_iso3   text,
    date_text      text,
    year           integer,
    value          double precision,
    unit           text,
    obs_status     text,
    decimals       integer,
    PRIMARY KEY (run_id, source_file, row_num)
);
CREATE INDEX worldbank_observation_key ON staging.worldbank_observation (run_id, indicator_id, country_iso3, year);

CREATE TABLE staging.unicef_observation (
    run_id          text    NOT NULL,
    source_file     text    NOT NULL,
    row_num         integer NOT NULL,
    ref_area        text,
    indicator       text,
    sex             text,
    age             text,
    wealth_quintile text,
    residence       text,
    data_source     text,
    unit_measure    text,
    time_period     text,
    year            integer,
    value_text      text,
    obs_value       double precision,
    lower_bound     double precision,
    upper_bound     double precision,
    obs_status      text,
    obs_conf        text,
    series_footnote text,
    obs_footnote    text,
    extra           jsonb,   -- remaining source columns; they differ between UNICEF dataflows
    PRIMARY KEY (run_id, source_file, row_num)
);
CREATE INDEX unicef_observation_key ON staging.unicef_observation (run_id, indicator, ref_area, year);
