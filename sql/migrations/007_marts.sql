-- Analytical marts, built from the core layer for one snapshot (run_id) at a time. Each table
-- documents its grain; definitions and caveats are in docs/analytics.md.

-- One reconciled value per country, indicator and year, carrying how far it can be trusted.
CREATE TABLE marts.country_indicator_year (
    run_id              text     NOT NULL,
    country_key         smallint NOT NULL REFERENCES core.dim_country,
    indicator_key       smallint NOT NULL REFERENCES core.dim_indicator,
    year                integer  NOT NULL,
    value               double precision NOT NULL,
    reconciled_source   text     NOT NULL,
    n_sources           integer  NOT NULL,
    n_evidence_groups   integer  NOT NULL,
    -- cross_validated: 2+ independent evidence groups agree within tolerance
    -- single_evidence_group: all sources republish one estimate, so it cannot be cross-checked
    -- conflict: independent evidence groups disagree beyond tolerance
    quality_tier        text     NOT NULL,
    outlier_flag        boolean  NOT NULL,   -- the reconciled source's row is a temporal outlier
    source_disagreement boolean  NOT NULL,   -- sources of one group differ beyond tolerance
    PRIMARY KEY (run_id, country_key, indicator_key, year)
);

-- Latest observation per country and indicator, and how much history sits behind it.
CREATE TABLE marts.country_latest (
    run_id              text     NOT NULL,
    country_key         smallint NOT NULL REFERENCES core.dim_country,
    indicator_key       smallint NOT NULL REFERENCES core.dim_indicator,
    latest_year         integer  NOT NULL,
    value               double precision NOT NULL,
    quality_tier        text     NOT NULL,
    first_year          integer  NOT NULL,
    n_observations      integer  NOT NULL,
    years_behind        integer  NOT NULL,   -- latest year anywhere for this indicator minus this country's
    PRIMARY KEY (run_id, country_key, indicator_key)
);

-- Change over the window since 2000. Both a linear slope (units per year, suitable for
-- percentages) and a log-linear annual rate (suitable for rates that fall proportionally).
CREATE TABLE marts.trend (
    run_id                text     NOT NULL,
    country_key           smallint NOT NULL REFERENCES core.dim_country,
    indicator_key         smallint NOT NULL REFERENCES core.dim_indicator,
    window_start          integer  NOT NULL,
    window_end            integer  NOT NULL,
    n_points              integer  NOT NULL,
    value_start           double precision NOT NULL,
    value_end             double precision NOT NULL,
    absolute_change       double precision NOT NULL,
    linear_slope_per_year double precision,
    linear_r2             double precision,
    annual_pct_change     double precision,   -- log-linear; NULL if any value in the window is not positive
    PRIMARY KEY (run_id, country_key, indicator_key)
);

-- Unweighted sub-regional summaries: the average COUNTRY, not the average person.
CREATE TABLE marts.region_year (
    run_id         text     NOT NULL,
    un_subregion   text     NOT NULL,
    indicator_key  smallint NOT NULL REFERENCES core.dim_indicator,
    year           integer  NOT NULL,
    n_countries    integer  NOT NULL,
    n_possible     integer  NOT NULL,
    coverage_share double precision NOT NULL,
    mean_value     double precision NOT NULL,
    median_value   double precision NOT NULL,
    min_value      double precision NOT NULL,
    max_value      double precision NOT NULL,
    p25_value      double precision NOT NULL,
    p75_value      double precision NOT NULL,
    PRIMARY KEY (run_id, un_subregion, indicator_key, year)
);

-- Rank (Spearman) association between two indicators across countries. Associations only: they
-- are cross-country, ecological, and say nothing about cause.
CREATE TABLE marts.indicator_association (
    run_id        text     NOT NULL,
    indicator_a   smallint NOT NULL REFERENCES core.dim_indicator,
    indicator_b   smallint NOT NULL REFERENCES core.dim_indicator,
    basis         text     NOT NULL,         -- level | change
    reference     text     NOT NULL,         -- for example '2015 +-3y' or '2000 to 2015 +-3y'
    n_countries   integer  NOT NULL,
    spearman_rho  double precision NOT NULL,
    pearson_r     double precision,
    rho_ci_low    double precision,          -- approximate 95% interval (Fisher z)
    rho_ci_high   double precision,
    PRIMARY KEY (run_id, indicator_a, indicator_b, basis)
);

-- Gaps between groups, computed only from rows of the same survey or series so that unlike
-- sources are never compared. a vs b: poorest vs richest, female vs male, rural vs urban.
CREATE TABLE marts.equity_gap (
    run_id            text     NOT NULL,
    country_key       smallint NOT NULL REFERENCES core.dim_country,
    indicator_key     smallint NOT NULL REFERENCES core.dim_indicator,
    year              integer  NOT NULL,
    dimension         text     NOT NULL,     -- wealth | sex | residence
    group_a           text     NOT NULL,
    group_b           text     NOT NULL,
    value_a           double precision NOT NULL,
    value_b           double precision NOT NULL,
    ratio             double precision,      -- a / b
    difference        double precision NOT NULL, -- a - b
    source            text     NOT NULL,
    upstream_label    text,
    n_candidate_pairs integer  NOT NULL,     -- series pairs available; one is chosen by rule
    PRIMARY KEY (run_id, country_key, indicator_key, year, dimension)
);

-- How far each indicator can be trusted, from the reconciled panel.
CREATE TABLE marts.data_trust (
    run_id                    text     NOT NULL,
    indicator_key             smallint NOT NULL REFERENCES core.dim_indicator,
    n_values                  integer  NOT NULL,
    n_countries               integer  NOT NULL,
    year_min                  integer  NOT NULL,
    year_max                  integer  NOT NULL,
    grid_coverage             double precision NOT NULL,   -- share of (countries x snapshot years)
    share_cross_validated     double precision NOT NULL,
    share_single_evidence     double precision NOT NULL,
    share_conflict            double precision NOT NULL,
    share_outlier_flag        double precision NOT NULL,
    share_source_disagreement double precision NOT NULL,
    PRIMARY KEY (run_id, indicator_key)
);

CREATE TABLE marts.build_log (
    run_id           text PRIMARY KEY,
    built_at         timestamptz NOT NULL DEFAULT now(),
    rows             jsonb       NOT NULL,       -- row count per mart
    parameters       jsonb       NOT NULL,
    duration_seconds double precision
);

-- Convenience views over the most recently built snapshot, with dimensions attached.
CREATE VIEW marts.latest_run AS
SELECT run_id FROM marts.build_log ORDER BY built_at DESC LIMIT 1;

CREATE VIEW marts.v_panel AS
SELECT p.run_id, c.iso3, c.name AS country, c.un_subregion, c.who_region_code,
       i.concept, i.name AS indicator, i.unit, p.year, p.value, p.quality_tier,
       p.outlier_flag, p.source_disagreement, p.n_sources, p.n_evidence_groups, p.reconciled_source
FROM marts.country_indicator_year p
JOIN core.dim_country c USING (country_key) JOIN core.dim_indicator i USING (indicator_key)
WHERE p.run_id = (SELECT run_id FROM marts.latest_run);

CREATE VIEW marts.v_country_latest AS
SELECT l.run_id, c.iso3, c.name AS country, c.un_subregion, i.concept, i.name AS indicator, i.unit,
       l.latest_year, l.value, l.quality_tier, l.first_year, l.n_observations, l.years_behind
FROM marts.country_latest l
JOIN core.dim_country c USING (country_key) JOIN core.dim_indicator i USING (indicator_key)
WHERE l.run_id = (SELECT run_id FROM marts.latest_run);
