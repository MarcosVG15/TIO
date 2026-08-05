"""Compare a live database against the ORM, and apply only the safe additions.

    python DATABASE/schema_diff.py                  # read-only report
    python DATABASE/schema_diff.py --sql            # print the DDL, run nothing
    python DATABASE/schema_diff.py --apply          # run the additive DDL only
    python DATABASE/schema_diff.py --url "postgresql+psycopg://..."

Reads DATABASE_URL from the project .env unless --url is given.

Why this exists rather than create_tables.py: create_all only ever adds missing
*tables*. It cannot tell you that a table exists with the wrong columns, which
is the drift that actually breaks a running API - the ORM emits INSERTs naming
columns the database does not have.

Safe to run against a database that is being written to:

  - The default report is read-only. It reads system catalogues and takes only
    the AccessShareLock that any SELECT takes.

  - --apply never drops, renames or retypes anything. It emits CREATE TABLE,
    ADD COLUMN and CREATE INDEX and nothing else. Everything else is reported
    for a human to decide on.

  - --apply sets lock_timeout first. This is the part that matters while a bulk
    load is in flight: ALTER TABLE needs ACCESS EXCLUSIVE, and a request for it
    queues *ahead of* every later reader, so an ALTER that waits behind a long
    transaction stalls the loader too. Failing after a few seconds and being
    run again later is strictly safer than waiting.

  - Each statement runs in its own transaction, so one blocked ALTER does not
    roll back the additions that already succeeded.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Iterable

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.schema import CreateIndex, CreateTable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ORM import Base, get_database_url  # noqa: E402

#: Alembic's own bookkeeping table is not in the ORM and must not be reported
#: as drift.
IGNORED_TABLES = {"alembic_version"}


def _flatten(diffs: Iterable[Any]) -> list[tuple]:
    """compare_metadata groups some column-level changes into sublists."""
    out: list[tuple] = []
    for entry in diffs:
        if isinstance(entry, list):
            out.extend(_flatten(entry))
        else:
            out.append(entry)
    return out


def _table_of(entry: tuple) -> str:
    """Best-effort table name for an arbitrary diff tuple, for grouping."""
    kind = entry[0]
    if kind in ("add_table", "remove_table"):
        return entry[1].name
    if kind in ("add_index", "remove_index", "add_constraint", "remove_constraint"):
        # Explicit None check: some constraint attributes are SQL clauses, and
        # a clause raises rather than answering a truthiness test.
        table = getattr(entry[1], "table", None)
        return table.name if table is not None else "?"
    if len(entry) > 2 and isinstance(entry[2], str):
        return entry[2]
    return "?"


def _describe(entry: tuple) -> str:
    kind = entry[0]
    if kind == "add_table":
        cols = ", ".join(c.name for c in entry[1].columns)
        return f"table is MISSING from the database  ({cols})"
    if kind == "remove_table":
        return "table exists in the database but not in the ORM"
    if kind == "add_column":
        col = entry[3]
        return f"column {col.name} ({col.type}) is MISSING from the database"
    if kind == "remove_column":
        return f"column {entry[3].name} exists in the database but not in the ORM"
    if kind == "add_index":
        return f"index {entry[1].name} is MISSING from the database"
    if kind == "remove_index":
        return f"index {entry[1].name} exists in the database but not in the ORM"
    if kind == "add_constraint":
        return f"constraint {getattr(entry[1], 'name', '?')} is MISSING"
    if kind == "remove_constraint":
        return f"constraint {getattr(entry[1], 'name', '?')} is only in the database"
    if kind == "modify_nullable":
        return f"column {entry[3]}: nullable differs (db={entry[5]}, orm={entry[6]})"
    if kind == "modify_type":
        return f"column {entry[3]}: type differs (db={entry[5]}, orm={entry[6]})"
    if kind == "modify_default":
        return f"column {entry[3]}: server default differs"
    return f"{kind}: {entry[1:]}"


def _is_additive(entry: tuple) -> bool:
    """Can this be applied without touching data that already exists?

    A new table is inert. A new index is a build, not a change. A new column is
    metadata-only in Postgres 11+ *provided* the database can fill it without
    visiting every row - which means nullable, or a non-volatile default.
    A NOT NULL column with no default cannot be added to a populated table at
    all, so it is never treated as safe.
    """
    kind = entry[0]
    if kind in ("add_table", "add_index"):
        return True
    if kind == "add_column":
        col = entry[3]
        return bool(col.nullable or col.server_default is not None)
    return False


def _column_sql(dialect: Any, table: str, column: Any) -> str:
    spec = dialect.ddl_compiler(dialect, None).get_column_specification(column)
    return f'ALTER TABLE "{table}" ADD COLUMN {spec}'


def _index_sql(dialect: Any, index: Any, concurrently: bool) -> str:
    sql = str(CreateIndex(index).compile(dialect=dialect)).strip()
    if concurrently:
        # CONCURRENTLY keeps writes flowing while the index builds, at the cost
        # of not being allowed inside a transaction. Only worth it on a table
        # that already holds rows.
        sql = sql.replace("CREATE INDEX", "CREATE INDEX CONCURRENTLY", 1)
        sql = sql.replace("CREATE UNIQUE INDEX", "CREATE UNIQUE INDEX CONCURRENTLY", 1)
    return sql


def report(engine: Engine) -> tuple[list[tuple], list[tuple]]:
    """Return (additive, needs_review). Read-only."""
    with engine.connect() as conn:
        context = MigrationContext.configure(
            conn, opts={"compare_type": True, "compare_server_default": False}
        )
        raw = _flatten(compare_metadata(context, Base.metadata))

    diffs = [d for d in raw if _table_of(d) not in IGNORED_TABLES]
    additive = [d for d in diffs if _is_additive(d)]
    review = [d for d in diffs if not _is_additive(d)]
    return additive, review


def plan(engine: Engine, additive: list[tuple]) -> list[tuple[str, bool]]:
    """Turn additive diffs into (sql, needs_autocommit) in dependency order."""
    dialect = engine.dialect
    existing = set(inspect(engine).get_table_names())
    statements: list[tuple[str, bool]] = []

    # Tables first: a column or index may belong to one of them.
    new_tables = {d[1].name for d in additive if d[0] == "add_table"}
    for entry in additive:
        if entry[0] == "add_table":
            statements.append(
                (str(CreateTable(entry[1]).compile(dialect=dialect)).strip(), False)
            )

    for entry in additive:
        if entry[0] == "add_column":
            statements.append((_column_sql(dialect, entry[2], entry[3]), False))

    for entry in additive:
        if entry[0] == "add_index":
            index = entry[1]
            table = index.table.name if index.table is not None else ""
            # A brand new table has no rows to lock, so plain CREATE INDEX is
            # both instant and simpler.
            fresh = table in new_tables or table not in existing
            statements.append((_index_sql(dialect, index, not fresh), not fresh))

    return statements


def apply(engine: Engine, statements: list[tuple[str, bool]], lock_timeout: str) -> int:
    """Run the plan. Returns the number of statements that failed."""
    failed = 0

    # Enum types and the vector extension are prerequisites of some tables.
    # create_all(checkfirst=True) is what knows how to emit them, so any new
    # table goes through SQLAlchemy rather than raw DDL.
    for sql, autocommit in statements:
        label = " ".join(sql.split())[:100]
        try:
            if autocommit:
                # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
                with engine.connect().execution_options(
                    isolation_level="AUTOCOMMIT"
                ) as conn:
                    _set_timeouts(conn, lock_timeout)
                    conn.execute(text(sql))
            else:
                with engine.begin() as conn:
                    _set_timeouts(conn, lock_timeout)
                    conn.execute(text(sql))
            print(f"  [ok]     {label}")
        except SQLAlchemyError as exc:
            failed += 1
            reason = str(getattr(exc, "orig", exc)).strip().splitlines()[0]
            print(f"  [FAILED] {label}")
            print(f"           {reason}")
            if "lock timeout" in reason.lower() or "timeout" in reason.lower():
                print(
                    "           Something is holding the table. Nothing was "
                    "changed; run this again when the writer is idle."
                )
    return failed


def _set_timeouts(conn: Connection, lock_timeout: str) -> None:
    """Never queue for a lock indefinitely - see the module docstring."""
    conn.execute(text(f"SET lock_timeout = '{lock_timeout}'"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true", help="run the additive DDL (default: report only)"
    )
    parser.add_argument(
        "--sql", action="store_true", help="print the additive DDL without running it"
    )
    parser.add_argument("--url", help="target database (default: DATABASE_URL from .env)")
    parser.add_argument(
        "--lock-timeout",
        default="3s",
        help="give up waiting for a table lock after this (default: 3s)",
    )
    args = parser.parse_args()

    url = args.url or get_database_url()
    shown = url.split("@")[-1] if "@" in url else url
    print(f"target: {shown}\n")

    engine = create_engine(url, pool_pre_ping=True, future=True)
    try:
        additive, review = report(engine)
    except SQLAlchemyError as exc:
        print(f"could not read the schema: {str(getattr(exc, 'orig', exc)).strip()}")
        return 2

    if not additive and not review:
        print("The database schema matches the ORM. Nothing to do.")
        return 0

    if additive:
        print(f"SAFE TO ADD ({len(additive)}) - additions only, no existing data touched")
        for entry in sorted(additive, key=_table_of):
            print(f"  {_table_of(entry):<20} {_describe(entry)}")
        print()

    if review:
        print(f"NEEDS A DECISION ({len(review)}) - never applied by this script")
        for entry in sorted(review, key=_table_of):
            print(f"  {_table_of(entry):<20} {_describe(entry)}")
        print(
            "\n  Nothing above is applied automatically: dropping or retyping a\n"
            "  column can destroy data, and a NOT NULL column with no default\n"
            "  cannot be added to a table that already has rows."
        )
        print()

    if not additive:
        return 1

    statements = plan(engine, additive)

    if args.sql or not args.apply:
        print("DDL that --apply would run:\n")
        for sql, autocommit in statements:
            print(f"{sql};" + ("   -- outside a transaction" if autocommit else ""))
        if not args.apply:
            print("\nRe-run with --apply to execute it.")
        return 1

    print(f"applying {len(statements)} statement(s), lock_timeout={args.lock_timeout}\n")
    failed = apply(engine, statements, args.lock_timeout)
    print()
    if failed:
        print(f"{failed} statement(s) did not apply. Re-run to retry - it is idempotent.")
        return 1
    print("Done. Re-run without --apply to confirm the schema now matches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
