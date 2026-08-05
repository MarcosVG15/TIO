"""Create (or reset) the TIO schema.

    python DATABASE/create_tables.py            # create anything missing
    python DATABASE/create_tables.py --drop     # DROP everything, recreate
    python DATABASE/create_tables.py --echo     # print the emitted SQL
    python DATABASE/create_tables.py --dry-run  # print DDL, touch nothing

Reads DATABASE_URL from the project .env.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.schema import CreateTable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ORM import Base, EMBEDDING_DIM, get_engine  # noqa: E402


def enable_pgvector(engine: Engine) -> None:
    """Must run before any vector(n) column is created."""
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    print(f"[ok]   pgvector available (embedding dim = {EMBEDDING_DIM})")


def print_ddl() -> None:
    """Compile against the Postgres dialect - no driver, no connection."""
    dialect = postgresql.dialect()
    for table in Base.metadata.sorted_tables:
        print(str(CreateTable(table).compile(dialect=dialect)).strip() + ";\n")


def drop_everything(engine: Engine) -> None:
    """Drop every table and enum in the public schema.

    Not Base.metadata.drop_all(): that only knows about models currently in
    ORM.py, so a table whose model has been deleted survives as an orphan -
    and its foreign keys then block dropping the tables that ARE known
    ("cannot drop table accounts because other objects depend on it").

    CASCADE handles the ordering, so no dependency sort is needed. Enum types
    are dropped separately because Postgres does not remove them with their
    tables. Only typtype='e' is targeted, leaving pgvector's `vector` type
    and the extension itself intact.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                DO $$
                DECLARE r RECORD;
                BEGIN
                    FOR r IN
                        SELECT tablename FROM pg_tables WHERE schemaname = 'public'
                    LOOP
                        EXECUTE format('DROP TABLE IF EXISTS public.%I CASCADE',
                                       r.tablename);
                    END LOOP;

                    FOR r IN
                        SELECT t.typname
                        FROM pg_type t
                        JOIN pg_namespace n ON n.oid = t.typnamespace
                        WHERE n.nspname = 'public' AND t.typtype = 'e'
                    LOOP
                        EXECUTE format('DROP TYPE IF EXISTS public.%I CASCADE',
                                       r.typname);
                    END LOOP;
                END $$;
                """
            )
        )


def report(engine: Engine) -> None:
    existing = set(inspect(engine).get_table_names())
    print("\nTables:")
    for table in Base.metadata.sorted_tables:
        mark = "OK  " if table.name in existing else "MISS"
        print(f"  [{mark}] {table.name:<20} ({len(table.columns)} columns)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the TIO database schema.")
    parser.add_argument(
        "--drop",
        action="store_true",
        help="DROP every table first (destroys all data), then recreate.",
    )
    parser.add_argument("--echo", action="store_true", help="Log the emitted SQL.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the CREATE TABLE statements without connecting.",
    )
    parser.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt for --drop."
    )
    args = parser.parse_args()

    if args.dry_run:
        print_ddl()
        return 0

    try:
        engine = get_engine(echo=args.echo)
    except RuntimeError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    print(f"[info] target: {engine.url.render_as_string(hide_password=True)}")

    if args.drop and not args.yes:
        if input("This DESTROYS all data in that database. Type 'drop': ").strip().lower() != "drop":
            print("[abort] cancelled")
            return 1

    try:
        enable_pgvector(engine)
        if args.drop:
            print("[drop] dropping every table and enum in the public schema ...")
            drop_everything(engine)
        Base.metadata.create_all(engine)
        print("[ok]   schema created")
        report(engine)
    except SQLAlchemyError as exc:
        print(f"\n[error] {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
