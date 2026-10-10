"""The data dictionary must cover every table and view, and must not describe things that do not exist."""
import pytest

from healthbridge import dictionary
from healthbridge.dictionary import COLUMNS, TABLES


@pytest.mark.integration
def test_every_table_and_view_is_described_and_every_description_is_current(pg_conn):
    assert dictionary.undocumented(pg_conn) == [], "add a description to src/healthbridge/dictionary.py"
    existing = dictionary._columns(pg_conn)
    assert set(TABLES) <= set(existing), "a described table no longer exists"
    all_columns = {name for cols in existing.values() for name, _, _ in cols}
    assert set(COLUMNS) <= all_columns, "a column note refers to a column that no longer exists"


@pytest.mark.integration
def test_render_lists_every_column_and_flags_the_empty_ml_schema(pg_conn):
    text = dictionary.render(pg_conn)
    for table, cols in dictionary._columns(pg_conn).items():
        assert f"### `{table}`" in text
        assert all(f"| `{name}` |" in text for name, _, _ in cols)
    assert "`ml` schema is reserved but empty" in text
