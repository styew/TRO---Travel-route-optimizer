"""Tests for the modular station identity and regional↔long-distance join."""

from __future__ import annotations

import csv
import tempfile
import unittest
import zipfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.session import Base
from backend.rail.countries.germany import RailNetworkManifest
from backend.rail import station_registry


def _write_gtfs_archive(path: Path, stops: list[dict[str, str]]) -> None:
    """Create the smallest valid GTFS stop archive required by the registry."""
    with zipfile.ZipFile(path, "w") as archive:
        with archive.open("stops.txt", "w") as raw_file:
            writer = csv.DictWriter(
                raw_file,
                fieldnames=["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type"],
            )
            raw_file.write("stop_id,stop_name,stop_lat,stop_lon,location_type\n".encode())
            for stop in stops:
                raw_file.write(",".join(
                    stop.get(field, "") for field in writer.fieldnames
                ).encode() + b"\n")


class GermanStationRegistryTests(unittest.TestCase):
    """Verify that one building gets an explicit handover across two GTFS feeds."""

    def test_matches_equivalent_parent_stations_and_limits_candidates(self) -> None:
        """Aachen Hbf / Aachen, Hbf becomes one canonical transfer station."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            regional_path = temporary_path / "regional.zip"
            long_distance_path = temporary_path / "long-distance.zip"
            _write_gtfs_archive(regional_path, [
                {"stop_id": "regional-aachen", "stop_name": "Aachen Hbf", "stop_lat": "50.7678", "stop_lon": "6.0915", "location_type": "1"},
                {"stop_id": "regional-other", "stop_name": "Other", "stop_lat": "51.0", "stop_lon": "6.0", "location_type": "1"},
            ])
            _write_gtfs_archive(long_distance_path, [
                {"stop_id": "long-aachen", "stop_name": "Aachen, Hauptbahnhof", "stop_lat": "50.7678", "stop_lon": "6.0915", "location_type": "1"},
            ])
            original_networks = station_registry.GERMANY_NETWORKS
            original_regional = station_registry.GERMANY_REGIONAL_NETWORK
            original_long_distance = station_registry.GERMANY_LONG_DISTANCE_NETWORK
            regional = RailNetworkManifest("de-regional", "Regional", str(regional_path), "d-ticket")
            long_distance = RailNetworkManifest("de-long-distance", "Long distance", str(long_distance_path), "paid")
            station_registry.GERMANY_NETWORKS = (regional, long_distance)
            station_registry.GERMANY_REGIONAL_NETWORK = regional
            station_registry.GERMANY_LONG_DISTANCE_NETWORK = long_distance
            try:
                engine = create_engine("sqlite://")
                Base.metadata.create_all(engine)
                session = sessionmaker(bind=engine)()
                summary = station_registry.build_germany_station_registry(session)
                candidates = station_registry.nearest_interchanges(session, 50.7678, 6.0915, limit=10)
            finally:
                station_registry.GERMANY_NETWORKS = original_networks
                station_registry.GERMANY_REGIONAL_NETWORK = original_regional
                station_registry.GERMANY_LONG_DISTANCE_NETWORK = original_long_distance

        self.assertEqual(summary.stations, 2)
        self.assertEqual(summary.memberships, 3)
        self.assertEqual(summary.interchanges, 2)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].from_stop_id, "regional-aachen")
        self.assertEqual(candidates[0].to_stop_id, "long-aachen")
        self.assertEqual(candidates[0].minimum_transfer_minutes, 15)


if __name__ == "__main__":
    unittest.main()
