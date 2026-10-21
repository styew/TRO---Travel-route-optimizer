"""Filter staged national GTFS archives to rail-only inputs for a shared OTP graph.

The script does not merge feeds: OpenTripPlanner keeps each archive's feed
identifier.  This preserves provenance and prevents stop IDs from different
countries from colliding. Cross-border station links are a later, explicit
normalisation step rather than a risky name-based automatic merge.
"""

from __future__ import annotations

import csv
import io
import argparse
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

# GTFS core uses route_type 2 for rail. The extended European route taxonomy
# uses the 100–117 range for railway services (high-speed, intercity, regional
# and other rail variants), which is what the Swiss national feed publishes.
RAIL_ROUTE_TYPES = {"2", *(str(value) for value in range(100, 118))}


def read_rows(archive: zipfile.ZipFile, name: str) -> tuple[list[str], list[dict[str, str]]]:
    """Read a required GTFS CSV while retaining its declared column order."""
    with archive.open(name) as source:
        reader = csv.DictReader(io.TextIOWrapper(source, encoding="utf-8-sig"))
        return reader.fieldnames or [], list(reader)


def write_rows(destination: zipfile.ZipFile, name: str, fields: list[str], rows: list[dict[str, str]]) -> None:
    """Write a UTF-8 GTFS CSV with predictable line endings."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    destination.writestr(name, output.getvalue().encode("utf-8"))


def csv_reader(archive: zipfile.ZipFile, name: str):
    """Open a UTF-8 GTFS table as a dictionary reader for streaming passes."""
    return io.TextIOWrapper(archive.open(name), encoding="utf-8-sig")


def build_feed(source_path: Path, destination_path: Path) -> tuple[int, int]:
    """Keep only GTFS rail routes plus the records needed to route them."""
    with zipfile.ZipFile(source_path) as source:
        required = {"agency.txt", "routes.txt", "trips.txt", "stop_times.txt", "stops.txt"}
        missing = required - set(source.namelist())
        if missing:
            raise ValueError(f"{source_path.name} is not a usable GTFS archive; missing {', '.join(sorted(missing))}.")
        if not {"calendar.txt", "calendar_dates.txt"} & set(source.namelist()):
            raise ValueError(f"{source_path.name} has no GTFS service calendar.")
        agency_fields, agencies = read_rows(source, "agency.txt")
        route_fields, routes = read_rows(source, "routes.txt")
        rail_routes = [route for route in routes if route.get("route_type") in RAIL_ROUTE_TYPES]
        route_ids = {route["route_id"] for route in rail_routes}
        used_agencies = {route.get("agency_id") for route in rail_routes}
        rail_agencies = [row for row in agencies if row.get("agency_id") in used_agencies]

        with tempfile.TemporaryDirectory(prefix="tro-gtfs-") as temporary_root:
            temporary_directory = Path(temporary_root)
            database = sqlite3.connect(temporary_directory / "selection.sqlite")
            database.executescript("""
                PRAGMA journal_mode=OFF;
                PRAGMA synchronous=OFF;
                CREATE TABLE trips (id TEXT PRIMARY KEY, service_id TEXT NOT NULL);
                CREATE TABLE stops (id TEXT PRIMARY KEY);
            """)
            trips_path = temporary_directory / "trips.txt"
            with csv_reader(source, "trips.txt") as source_file, trips_path.open("w", newline="", encoding="utf-8") as output:
                reader = csv.DictReader(source_file)
                trip_fields = reader.fieldnames or []
                writer = csv.DictWriter(output, fieldnames=trip_fields, lineterminator="\n")
                writer.writeheader()
                trip_count = 0
                for row in reader:
                    if row.get("route_id") in route_ids:
                        writer.writerow(row)
                        database.execute("INSERT INTO trips VALUES (?, ?)", (row["trip_id"], row["service_id"]))
                        trip_count += 1
                        if trip_count % 10_000 == 0:
                            database.commit()
                database.commit()
            stop_times_path = temporary_directory / "stop_times.txt"
            with csv_reader(source, "stop_times.txt") as source_file, stop_times_path.open("w", newline="", encoding="utf-8") as output:
                reader = csv.DictReader(source_file)
                stop_time_fields = reader.fieldnames or []
                writer = csv.DictWriter(output, fieldnames=stop_time_fields, lineterminator="\n")
                writer.writeheader()
                for index, row in enumerate(reader, start=1):
                    if database.execute("SELECT 1 FROM trips WHERE id = ?", (row.get("trip_id"),)).fetchone():
                        writer.writerow(row)
                        database.execute("INSERT OR IGNORE INTO stops VALUES (?)", (row.get("stop_id"),))
                    if index % 25_000 == 0:
                        database.commit()
                database.commit()
            # A first streaming pass discovers parent stations; the second
            # writes selected platforms and their parents without storing all
            # Swiss stops in Python memory.
            with csv_reader(source, "stops.txt") as source_file:
                for row in csv.DictReader(source_file):
                    if database.execute("SELECT 1 FROM stops WHERE id = ?", (row.get("stop_id"),)).fetchone() and row.get("parent_station"):
                        database.execute("INSERT OR IGNORE INTO stops VALUES (?)", (row["parent_station"],))
                database.commit()
            stops_path = temporary_directory / "stops.txt"
            with csv_reader(source, "stops.txt") as source_file, stops_path.open("w", newline="", encoding="utf-8") as output:
                reader = csv.DictReader(source_file)
                stop_fields = reader.fieldnames or []
                writer = csv.DictWriter(output, fieldnames=stop_fields, lineterminator="\n")
                writer.writeheader()
                for row in reader:
                    if database.execute("SELECT 1 FROM stops WHERE id = ?", (row.get("stop_id"),)).fetchone():
                        writer.writerow(row)
            calendar_paths: list[tuple[str, Path]] = []
            for calendar_name in ("calendar.txt", "calendar_dates.txt"):
                if calendar_name not in source.namelist():
                    continue
                calendar_path = temporary_directory / calendar_name
                with csv_reader(source, calendar_name) as source_file, calendar_path.open("w", newline="", encoding="utf-8") as output:
                    reader = csv.DictReader(source_file)
                    fields = reader.fieldnames or []
                    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
                    writer.writeheader()
                    for row in reader:
                        if database.execute("SELECT 1 FROM trips WHERE service_id = ? LIMIT 1", (row.get("service_id"),)).fetchone():
                            writer.writerow(row)
                calendar_paths.append((calendar_name, calendar_path))
            database.close()

            destination_path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(destination_path, "w", zipfile.ZIP_DEFLATED) as destination:
                write_rows(destination, "agency.txt", agency_fields, rail_agencies)
                write_rows(destination, "routes.txt", route_fields, rail_routes)
                destination.write(trips_path, "trips.txt")
                destination.write(stop_times_path, "stop_times.txt")
                destination.write(stops_path, "stops.txt")
                for calendar_name, calendar_path in calendar_paths:
                    destination.write(calendar_path, calendar_name)
                for name in ("feed_info.txt", "attributions.txt", "transfers.txt"):
                    if name in source.namelist():
                        destination.writestr(name, source.read(name))
    return len(rail_routes), trip_count


def main() -> None:
    """Build one OTP-ready rail-only archive per staged national source."""
    parser = argparse.ArgumentParser(
        description="Create rail-only GTFS inputs for the optional Europe graph."
    )
    parser.add_argument(
        "--country", choices=("france", "switzerland", "austria", "italy"),
        help="Build only one staged country; omit to rebuild every source.",
    )
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source_directory = root / "otp-data" / "europe" / "raw"
    destination_directory = root / "otp-data" / "europe" / "rail"
    sources = sorted(source_directory.glob("*.zip"))
    if arguments.country:
        sources = [source for source in sources if source.name.startswith(arguments.country)]
    if not sources:
        sys.exit("No staged European GTFS archives found. Run update_european_gtfs.ps1 first.")
    for source in sources:
        print(f"Processing {source.name}. This can take several minutes for a national feed.", flush=True)
        destination = destination_directory / source.name
        routes, trips = build_feed(source, destination)
        print(f"Created {destination.name}: {routes} rail routes, {trips} rail trips.", flush=True)


if __name__ == "__main__":
    main()
