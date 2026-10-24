"""Create reproducible pandas summaries from TRO's locally stored flight data.

The script is read-only for the SQLite database. It writes CSV reports to a
separate folder so analysis never changes route-search data or provider caches.
"""

import argparse
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd


def _read_only_connection(database_path: Path) -> sqlite3.Connection:
    """Open the existing database without permitting this analytics job to write to it."""
    if not database_path.is_file():
        raise FileNotFoundError(
            f"Database not found: {database_path}. Start Docker and run one search first."
        )
    return sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)


def _write_schedule_summary(connection: sqlite3.Connection, output_folder: Path) -> Path:
    """Summarize direct scheduled flights by date, route, and operating airline."""
    schedules = pd.read_sql_query(
        """SELECT service_date, origin_iata, destination_iata, airline_code,
                  airline_name, flight_number, planned_departure, planned_arrival,
                  source, imported_at
           FROM scheduled_flights""",
        connection,
    )
    output_path = output_folder / "scheduled_flights_summary.csv"
    if schedules.empty:
        pd.DataFrame(columns=[
            "service_date", "origin_iata", "destination_iata", "airline_code",
            "airline_name", "scheduled_flight_count", "first_departure",
            "last_arrival", "most_recent_import",
        ]).to_csv(output_path, index=False)
        return output_path
    schedules["is_within_seven_day_cache"] = pd.to_datetime(
        schedules["imported_at"], errors="coerce"
    ) >= pd.Timestamp(datetime.now(UTC).replace(tzinfo=None) - timedelta(days=7))
    summary = schedules.groupby(
        ["service_date", "origin_iata", "destination_iata", "airline_code", "airline_name"],
        dropna=False,
    ).agg(
        scheduled_flight_count=("flight_number", "count"),
        first_departure=("planned_departure", "min"),
        last_arrival=("planned_arrival", "max"),
        most_recent_import=("imported_at", "max"),
        records_within_seven_day_cache=("is_within_seven_day_cache", "sum"),
    ).reset_index()
    summary.to_csv(output_path, index=False)
    return output_path


def _write_price_summary(connection: sqlite3.Connection, output_folder: Path) -> Path:
    """Summarize stored provider observations without mislabelling them as live fares."""
    observations = pd.read_sql_query(
        """SELECT origin_iata, destination_iata, departure_month, departure_weekday,
                  price_eur, transfer_count, duration_minutes, provider, observed_at
           FROM flight_price_observations""",
        connection,
    )
    output_path = output_folder / "flight_price_observations_summary.csv"
    if observations.empty:
        pd.DataFrame(columns=[
            "origin_iata", "destination_iata", "departure_month", "departure_weekday",
            "observation_count", "low_eur", "average_eur", "high_eur",
            "latest_observed_at",
        ]).to_csv(output_path, index=False)
        return output_path
    summary = observations.groupby(
        ["origin_iata", "destination_iata", "departure_month", "departure_weekday"],
        dropna=False,
    ).agg(
        observation_count=("price_eur", "count"),
        low_eur=("price_eur", "min"),
        average_eur=("price_eur", "mean"),
        high_eur=("price_eur", "max"),
        latest_observed_at=("observed_at", "max"),
    ).reset_index()
    summary[["low_eur", "average_eur", "high_eur"]] = summary[
        ["low_eur", "average_eur", "high_eur"]
    ].round(2)
    summary.to_csv(output_path, index=False)
    return output_path


def main() -> None:
    """Generate CSV analysis artifacts without modifying the active database."""
    parser = argparse.ArgumentParser(description="Create pandas flight-data summaries for TRO.")
    parser.add_argument("--database", type=Path, default=Path("data/tro.db"))
    parser.add_argument("--output", type=Path, default=Path("data/analysis"))
    arguments = parser.parse_args()
    arguments.output.mkdir(parents=True, exist_ok=True)
    with _read_only_connection(arguments.database) as connection:
        schedule_report = _write_schedule_summary(connection, arguments.output)
        price_report = _write_price_summary(connection, arguments.output)
    print(f"Wrote {schedule_report}")
    print(f"Wrote {price_report}")


if __name__ == "__main__":
    main()
