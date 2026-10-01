-- Support for four additional quality checks: country-identifier resolution, schema validation,
-- temporal outlier flags and within-evidence-group source disagreement.

-- Same normalisation as healthbridge.reference.normalize_alias (tested for equivalence).
CREATE FUNCTION core.normalize_name(t text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT regexp_replace(
        lower(translate(t, 'àáâãäåçèéêëìíîïñòóôõöùúûüýÿ', 'aaaaaaceeeeiiiinooooouuuuyy')),
        '[^a-z0-9]', '', 'g')
$$;

-- How the country was identified: exact ISO3, or repaired by a documented fallback.
ALTER TABLE core.fact_observation
    ADD COLUMN country_resolution text NOT NULL DEFAULT 'exact';   -- exact | normalized | iso2 | alias

-- Columns present in a source file that the schema does not know about (reported, not fatal).
ALTER TABLE staging.load_log
    ADD COLUMN unexpected_columns text[] NOT NULL DEFAULT '{}';

-- Largest relative disagreement between sources of the SAME evidence group in one cell. Those
-- sources normally publish the same number, so a large gap is a data-source inconsistency.
ALTER TABLE core.fact_reconciled
    ADD COLUMN max_within_group_spread_rel double precision,
    ADD COLUMN within_group_disagreement boolean NOT NULL DEFAULT false;
