"""Import authorised airport or airline schedule exports into TRO's local database.

The importer deliberately accepts a local CSV instead of scraping a website. This
keeps the source auditable and lets an operator use an official export only when
its licence permits reuse.
"""

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

# Executing a script makes its own folder the import root. Add the project root
# so the importer works both locally and from /app inside the Docker image.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.database.repository import ScheduledFlightRepository
from backend.database.session import initialize_database

REQUIRED_COLUMNS = {
    "service_date", "flight_number", "origin_iata", "destination_iata",
    "planned_departure", "planned_arrival", "source",
}
OPTIONAL_COLUMNS = {"airline_code", "airline_name", "source_url", "source_updated_at"}


def _read_schedule_rows(path: Path) -> list[dict]:
    """Validate a CSV export and normalize it to the scheduled-flight schema."""
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        columns = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - columns
        if missing:
            raise ValueError("Missing CSV column(s): " + ", ".join(sorted(missing)))
        flights = []
        for row_number, row in enumerate(reader, start=2):
            try:
                service_date = row["service_date"].strip()
                datetime.fromisoformat(service_date)
                departure = row["planned_departure"].strip()
                arrival = row["planned_arrival"].strip()
                datetime.fromisoformat(departure)
                datetime.fromisoformat(arrival)
                origin_iata = row["origin_iata"].strip().upper()
                destination_iata = row["destination_iata"].strip().upper()
                if len(origin_iata) != 3 or len(destination_iata) != 3:
                    raise ValueError("IATA codes must contain exactly three letters")
                source_updated_at = row.get("source_updated_at", "").strip()
                flights.append({
                    "service_date": service_date,
                    "flight_number": row["flight_number"].strip().upper(),
                    "origin_iata": origin_iata,
                    "destination_iata": destination_iata,
                    "planned_departure": departure,
                    "planned_arrival": arrival,
                    "airline_code": row.get("airline_code", "").strip().upper() or None,
                    "airline_name": row.get("airline_name", "").strip() or None,
                    "source": row["source"].strip(),
                    "source_url": row.get("source_url", "").strip() or None,
                    "source_updated_at": datetime.fromisoformat(source_updated_at) if source_updated_at else None,
                })
            except (KeyError, ValueError) as error:
                raise ValueError(f"Invalid row {row_number}: {error}") from error
    return flights


def main() -> None:
    """Load a schedule CSV into SQLite and show a small, non-sensitive summary."""
    parser = argparse.ArgumentParser(description="Import authorised published flight schedules.")
    parser.add_argument("csv_file", type=Path, help="Path to the CSV file to import.")
    arguments = parser.parse_args()
    if not arguments.csv_file.is_file():
        raise SystemExit(f"CSV file not found: {arguments.csv_file}")
    flights = _read_schedule_rows(arguments.csv_file)
    initialize_database()
    inserted, updated = ScheduledFlightRepository().upsert_many(flights)
    print(f"Imported {len(flights)} scheduled flight rows ({inserted} new, {updated} refreshed).")


if __name__ == "__main__":
    main()
