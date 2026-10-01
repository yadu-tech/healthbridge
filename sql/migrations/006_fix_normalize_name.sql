-- Fix: lower-case BEFORE translating accents. Translating first left upper-case accented letters
-- (for example the O-circumflex in "COTE D'IVOIRE" with its accent) untouched, so SQL and
-- Python disagreed. Found by tests/test_checks.py::test_sql_and_python_name_normalisation_agree.
CREATE OR REPLACE FUNCTION core.normalize_name(t text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT regexp_replace(
        translate(lower(t), 'àáâãäåçèéêëìíîïñòóôõöùúûüýÿ', 'aaaaaaceeeeiiiinooooouuuuyy'),
        '[^a-z0-9]', '', 'g')
$$;
