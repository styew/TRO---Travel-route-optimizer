"""Create an OTP input feed containing only Deutschlandticket-compatible rail services."""

from __future__ import annotations

import csv
import io
import re
import sys
import zipfile
from pathlib import Path

LONG_DISTANCE_LINE = re.compile(r"^(ICE|IC|EC|FLX|TGV|RJ|NJ)", re.IGNORECASE)
LONG_DISTANCE_AGENCY = re.compile(r"flixtrain|db fernverkehr", re.IGNORECASE)


def read_rows(archive: zipfile.ZipFile, name: str) -> tuple[list[str], list[dict[str, str]]]:
    """Read one UTF-8 GTFS table and preserve its original column order."""
    with archive.open(name) as source:
        reader = csv.DictReader(io.TextIOWrapper(source, encoding="utf-8-sig"))
        return reader.fieldnames or [], list(reader)


def write_rows(archive: zipfile.ZipFile, name: str, fields: list[str], rows: list[dict[str, str]]) -> None:
    """Write one filtered GTFS table with standard CRLF CSV formatting."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    archive.writestr(name, buffer.getvalue().encode("utf-8"))


def is_d_ticket_route(route: dict[str, str], agencies: dict[str, str]) -> bool:
    """Exclude known long-distance rail services from a regional-rail source feed."""
    line = route.get("route_short_name", "")
    agency_name = agencies.get(route.get("agency_id", ""), "")
    return not LONG_DISTANCE_LINE.match(line) and not LONG_DISTANCE_AGENCY.search(agency_name)


def build(source_path: Path, destination_path: Path) -> None:
    """Filter routes, trips, stops, and calendars while retaining a valid GTFS archive."""
    with zipfile.ZipFile(source_path) as source:
        agency_fields, agency_rows = read_rows(source, "agency.txt")
        agency_names = {row["agency_id"]: row["agency_name"] for row in agency_rows}
        route_fields, route_rows = read_rows(source, "routes.txt")
        routes = [row for row in route_rows if is_d_ticket_route(row, agency_names)]
        route_ids = {row["route_id"] for row in routes}
        trip_fields, trip_rows = read_rows(source, "trips.txt")
        trips = [row for row in trip_rows if row["route_id"] in route_ids]
        trip_ids = {row["trip_id"] for row in trips}
        service_ids = {row["service_id"] for row in trips}
        stop_time_fields, stop_time_rows = read_rows(source, "stop_times.txt")
        stop_times = [row for row in stop_time_rows if row["trip_id"] in trip_ids]
        stop_ids = {row["stop_id"] for row in stop_times}
        stop_fields, stop_rows = read_rows(source, "stops.txt")
        parent_ids = {
            row["parent_station"]
            for row in stop_rows
            if row["stop_id"] in stop_ids and row.get("parent_station")
        }
        stops = [row for row in stop_rows if row["stop_id"] in stop_ids | parent_ids]
        calendar_fields, calendar_rows = read_rows(source, "calendar.txt")
        calendars = [row for row in calendar_rows if row["service_id"] in service_ids]
        date_fields, date_rows = read_rows(source, "calendar_dates.txt")
        calendar_dates = [row for row in date_rows if row["service_id"] in service_ids]
        used_agencies = {row["agency_id"] for row in routes}
        agencies = [row for row in agency_rows if row["agency_id"] in used_agencies]

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination_path, "w", zipfile.ZIP_DEFLATED) as destination:
            write_rows(destination, "agency.txt", agency_fields, agencies)
            write_rows(destination, "routes.txt", route_fields, routes)
            write_rows(destination, "trips.txt", trip_fields, trips)
            write_rows(destination, "stop_times.txt", stop_time_fields, stop_times)
            write_rows(destination, "stops.txt", stop_fields, stops)
            write_rows(destination, "calendar.txt", calendar_fields, calendars)
            write_rows(destination, "calendar_dates.txt", date_fields, calendar_dates)
            for name in ("feed_info.txt", "attributions.txt"):
                if name in source.namelist():
                    destination.writestr(name, source.read(name))
    print(f"Created {destination_path} with {len(routes)} routes and {len(trips)} trips.")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    source = root / "otp-data" / "regional-rail-gtfs.zip"
    destination = root / "otp-data" / "d-ticket" / "regional-d-ticket-gtfs.zip"
    if not source.exists():
        sys.exit(f"Missing source GTFS archive: {source}")
    build(source, destination)
