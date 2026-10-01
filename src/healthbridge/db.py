"""PostgreSQL connection and versioned schema migrations.

Connection settings come from the environment (or a local ``.env`` file):
POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, POSTGRES_HOST, POSTGRES_PORT.

Migrations are plain SQL files in ``sql/migrations`` named ``NNN_description.sql``. Each runs
once, in order, inside a transaction, and is recorded with its checksum. Editing an applied
migration is an error: schema changes are made by adding a new file.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "sql" / "migrations"


class MigrationError(RuntimeError):
    pass


def _load_dotenv(path: Path = REPO_ROOT / ".env") -> None:
    """Minimal .env reader; real environment variables take precedence."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def connect(dbname: str | None = None, autocommit: bool = False) -> psycopg.Connection:
    _load_dotenv()
    env = os.environ
    return psycopg.connect(
        # 127.0.0.1, not "localhost": Docker publishes IPv4 only, and on Windows "localhost" tries
        # IPv6 first, which stalls for ~2 minutes before falling back.
        host=env.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(env.get("POSTGRES_PORT", "5432")),
        user=env["POSTGRES_USER"],
        password=env["POSTGRES_PASSWORD"],
        dbname=dbname or env.get("POSTGRES_DB", "healthbridge"),
        autocommit=autocommit,
        connect_timeout=10,
    )


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def migrate(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations; return the versions applied in this call."""
    # Everything runs inside explicit transaction blocks, so nothing is left in an implicit
    # transaction (which would turn later blocks into uncommitted savepoints).
    with conn.transaction():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS public.schema_migrations ("
            " version text PRIMARY KEY, checksum text NOT NULL,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )
        applied = dict(
            conn.execute("SELECT version, checksum FROM public.schema_migrations").fetchall()
        )
    newly_applied: list[str] = []
    for path in sorted(directory.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        checksum = _checksum(sql)
        if path.stem in applied:
            if applied[path.stem] != checksum:
                raise MigrationError(
                    f"migration {path.name} was modified after it was applied; add a new migration"
                )
            continue
        with conn.transaction():
            conn.execute(sql)
            conn.execute(
                "INSERT INTO public.schema_migrations (version, checksum) VALUES (%s, %s)",
                (path.stem, checksum),
            )
        newly_applied.append(path.stem)
    return newly_applied
