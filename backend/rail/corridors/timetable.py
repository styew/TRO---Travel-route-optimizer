"""Build an isolated DE–FR SQLite timetable from SNCF cross-border rail trips.

This feed adapter recognizes SNCF UIC parent prefixes, not route/city lists.
Other corridors must supply their own country identification adapter. No trip
with an unknown or third-country calling point is admitted by this adapter.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def _rows(archive: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    """Read an optional GTFS table with BOM-safe CSV handling."""
    if name not in archive.namelist():
        return []
    with archive.open(name) as raw:
        return list(csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")))


def sncf_country(parent_id: str) -> str | None:
    """Recognize German/French UIC prefixes only in SNCF's StopArea namespace."""
    if parent_id.startswith("StopArea:OCE80"):
        return "DE"
    if parent_id.startswith("StopArea:OCE87"):
        return "FR"
    return None


def _seconds(value: str) -> int:
    """Parse GTFS time, retaining service-day hours beyond midnight."""
    hours, minutes, seconds = map(int, value.split(":"))
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ValueError(f"Invalid GTFS time: {value}")
    return hours * 3600 + minutes * 60 + seconds


def service_instant(day: date, seconds: int, timezone: str) -> datetime:
    """Interpret GTFS time from local noon minus twelve hours, including DST."""
    zone = ZoneInfo(timezone)
    noon = datetime.combine(day, time(12), zone)
    return datetime.fromtimestamp(noon.timestamp() - 43200 + seconds, zone)


def build_timetable(source: Path, target: Path) -> dict[str, int]:
    """Atomically publish rail trips serving both countries and their calendars.

    The source archive remains untouched. Pick-up/drop-off restrictions and
    service exceptions are retained; buses and third-country detours are
    excluded. Regional and high-speed trains use the same discovery path.
    """
    with zipfile.ZipFile(source) as archive:
        stops = {row["stop_id"]: row for row in _rows(archive, "stops.txt")}
        routes = {row["route_id"]: row for row in _rows(archive, "routes.txt")
                  if row["route_type"] == "2" or 100 <= int(row["route_type"]) < 200}
        trips = {row["trip_id"]: row for row in _rows(archive, "trips.txt")
                 if row["route_id"] in routes}
        # A frequency template is not an exact scheduled departure. Leave it
        # out until the corridor implements explicit frequency expansion.
        for row in _rows(archive, "frequencies.txt"):
            trips.pop(row["trip_id"], None)
        calls = defaultdict(list)
        with archive.open("stop_times.txt") as raw:
            for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                if row["trip_id"] in trips:
                    calls[row["trip_id"]].append(row)
        retained = {}
        for trip_id, rows in calls.items():
            ordered = sorted(rows, key=lambda row: int(row["stop_sequence"]))
            parents = [stops[row["stop_id"]].get("parent_station") or row["stop_id"] for row in ordered]
            if {sncf_country(parent) for parent in parents} == {"DE", "FR"}:
                retained[trip_id] = list(zip(ordered, parents))
        service_ids = {trips[key]["service_id"] for key in retained}
        dates: dict[str, set[str]] = defaultdict(set)
        weekdays = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        for row in _rows(archive, "calendar.txt"):
            if row["service_id"] not in service_ids:
                continue
            day = datetime.strptime(row["start_date"], "%Y%m%d").date()
            end = datetime.strptime(row["end_date"], "%Y%m%d").date()
            while day <= end:
                if row[weekdays[day.weekday()]] == "1":
                    dates[row["service_id"]].add(day.isoformat())
                day += timedelta(days=1)
        for row in _rows(archive, "calendar_dates.txt"):
            if row["service_id"] in service_ids:
                day = datetime.strptime(row["date"], "%Y%m%d").date().isoformat()
                if row["exception_type"] == "1":
                    dates[row["service_id"]].add(day)
                else:
                    dates[row["service_id"]].discard(day)

    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=target.parent, suffix=".sqlite")
    os.close(handle)
    try:
        with sqlite3.connect(temporary) as db:
            db.executescript("""
                CREATE TABLE stations (id TEXT PRIMARY KEY, name TEXT, lat REAL, lon REAL, country TEXT);
                CREATE TABLE trips (id TEXT PRIMARY KEY, service TEXT, line TEXT, name TEXT);
                CREATE TABLE calls (trip TEXT, sequence INTEGER, station TEXT, arrival INTEGER, departure INTEGER, pickup INTEGER, dropoff INTEGER);
                CREATE TABLE dates (service TEXT, day TEXT, PRIMARY KEY(service, day));
                CREATE INDEX calls_station ON calls(station, trip);
                CREATE INDEX dates_day ON dates(day, service);
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
            """)
            parent_ids = {parent for rows in retained.values() for _, parent in rows}
            db.executemany("INSERT INTO stations VALUES (?, ?, ?, ?, ?)", [
                (key, stops[key]["stop_name"], float(stops[key]["stop_lat"]), float(stops[key]["stop_lon"]), sncf_country(key))
                for key in sorted(parent_ids)
            ])
            for trip_id, rows in retained.items():
                trip = trips[trip_id]
                route = routes[trip["route_id"]]
                db.execute("INSERT INTO trips VALUES (?, ?, ?, ?)",
                           (trip_id, trip["service_id"], route.get("route_short_name", ""), route.get("route_long_name", "")))
                db.executemany("INSERT INTO calls VALUES (?, ?, ?, ?, ?, ?, ?)", [
                    (trip_id, int(row["stop_sequence"]), parent, _seconds(row["arrival_time"]), _seconds(row["departure_time"]),
                     int(row.get("pickup_type") or 0), int(row.get("drop_off_type") or 0))
                    for row, parent in rows
                ])
            db.executemany("INSERT INTO dates VALUES (?, ?)", [(service, day) for service in service_ids for day in dates[service]])
            summary = {"stations": len(parent_ids), "trips": len(retained), "service_days": sum(len(dates[s]) for s in service_ids)}
            digest = hashlib.sha256()
            with source.open("rb") as source_file:
                for block in iter(lambda: source_file.read(1024 * 1024), b""):
                    digest.update(block)
            db.execute("INSERT INTO metadata VALUES ('build', ?)", (json.dumps({
                "schema_version": 1, "source": str(source), "source_sha256": digest.hexdigest(), **summary,
            }),))
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return summary


def open_timetable(path: str) -> sqlite3.Connection:
    """Open the published index read-only; never silently create an empty index."""
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection
