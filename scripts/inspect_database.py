"""Inspect TRO's local SQLite database without changing its contents.

Use this tool for a quick terminal view of the cache, scheduled flights, and
price observations. It deliberately opens the database in SQLite read-only
mode, so it is safe to run while the backend container is serving searches.
"""

import argparse
import json
import sqlite3
from pathlib import Path


def _read_only_connection(database_path: Path) -> sqlite3.Connection:
    """Open an existing SQLite file in read-only mode and fail with useful text."""
    if not database_path.is_file():
        raise FileNotFoundError(
            f"Database not found: {database_path}. Start Docker and run one search first."
        )
    return sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)


def _format(value: object, maximum_length: int = 180) -> str:
    """Make long JSON cache fields readable without flooding a terminal."""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    text = str(value)
    return text if len(text) <= maximum_length else text[:maximum_length - 1] + "…"


def _table_names(connection: sqlite3.Connection) -> list[str]:
    """Return only user data tables, excluding SQLite internal metadata tables."""
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]


def _show_summary(connection: sqlite3.Connection, tables: list[str]) -> None:
    """Print a compact record count for every persistent TRO table."""
    print("TRO database summary (read-only)")
    for table in tables:
        count = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        print(f"- {table}: {count} row(s)")


def _show_table(connection: sqlite3.Connection, table: str, limit: int) -> None:
    """Print recent rows from one validated table name without unsafe SQL input."""
    columns = [
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    ]
    rows = connection.execute(
        f'SELECT * FROM "{table}" ORDER BY rowid DESC LIMIT ?', (limit,)
    ).fetchall()
    print(f"\n{table}: showing {len(rows)} recent row(s)")
    for position, row in enumerate(rows, start=1):
        values = "; ".join(
            f"{column}={_format(value)}" for column, value in zip(columns, row)
        )
        print(f"{position}. {values}")


def main() -> None:
    """Parse a small inspection request and keep all database operations read-only."""
    parser = argparse.ArgumentParser(description="Inspect the local TRO SQLite database safely.")
    parser.add_argument("--database", type=Path, default=Path("data/tro.db"))
    parser.add_argument("--table", help="Optional table name to display after the summary.")
    parser.add_argument("--limit", type=int, default=10, choices=range(1, 101))
    arguments = parser.parse_args()

    with _read_only_connection(arguments.database) as connection:
        tables = _table_names(connection)
        _show_summary(connection, tables)
        if arguments.table:
            if arguments.table not in tables:
                raise SystemExit(
                    f"Unknown table '{arguments.table}'. Choose one of: {', '.join(tables)}"
                )
            _show_table(connection, arguments.table, arguments.limit)


if __name__ == "__main__":
    main()
