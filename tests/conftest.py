"""Shared fixtures. Integration tests use a throwaway database and skip if Postgres is down."""
import os

import psycopg
import pytest

from healthbridge.db import connect, migrate

TEST_DB = "healthbridge_test"


@pytest.fixture(scope="session")
def pg_conn():
    try:
        admin = connect(dbname="postgres", autocommit=True)
    except (psycopg.OperationalError, KeyError) as exc:
        if os.environ.get("HEALTHBRIDGE_REQUIRE_DB"):  # CI: a missing database is a failure, not a skip
            raise
        pytest.skip(f"PostgreSQL not available: {exc}")
    admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
    admin.execute(f"CREATE DATABASE {TEST_DB}")
    conn = connect(dbname=TEST_DB)
    migrate(conn)
    yield conn
    conn.close()
    admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
    admin.close()
