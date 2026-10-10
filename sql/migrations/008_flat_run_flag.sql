-- Flat-run flag: a modelled mortality estimate that repeats exactly for several consecutive years
-- is a model feature, not a measurement. The flag is informational (see core/flat_runs.py).

ALTER TABLE marts.country_indicator_year
    ADD COLUMN flat_run boolean NOT NULL DEFAULT false;   -- the reconciled source's row sits in a flat run

ALTER TABLE marts.data_trust
    ADD COLUMN share_flat_run double precision NOT NULL DEFAULT 0;

-- Columns are appended at the end, as CREATE OR REPLACE VIEW requires.
CREATE OR REPLACE VIEW marts.v_panel AS
SELECT p.run_id, c.iso3, c.name AS country, c.un_subregion, c.who_region_code,
       i.concept, i.name AS indicator, i.unit, p.year, p.value, p.quality_tier,
       p.outlier_flag, p.source_disagreement, p.n_sources, p.n_evidence_groups, p.reconciled_source,
       p.flat_run
FROM marts.country_indicator_year p
JOIN core.dim_country c USING (country_key) JOIN core.dim_indicator i USING (indicator_key)
WHERE p.run_id = (SELECT run_id FROM marts.latest_run);
