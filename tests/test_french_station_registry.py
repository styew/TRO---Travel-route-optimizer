"""Regression tests for GTFS-backed multi-terminal French city selection."""

import unittest
from types import SimpleNamespace

from backend.rail.france_station_registry import _rank_city_hubs


class FranceStationRegistryTests(unittest.TestCase):
    """Ensure French city hubs are selected from timetable quality, not OSM order."""

    def test_prioritises_mainline_hubs_and_limits_the_city_set(self) -> None:
        """A high-service Part-Dieu-like station outranks closer local terminals."""
        city = {"city_name": "Lyon", "latitude": 45.758, "longitude": 4.832}
        records = [
            (SimpleNamespace(
                canonical_id="fr:local", name="Lyon Saint-Paul", latitude=45.766,
                longitude=4.827,
            ), SimpleNamespace(service_score=1, source_stop_id="StopArea:local")),
            (SimpleNamespace(
                canonical_id="fr:part-dieu", name="Lyon Part Dieu", latitude=45.761,
                longitude=4.859,
            ), SimpleNamespace(service_score=6, source_stop_id="StopArea:OCE87723197")),
            (SimpleNamespace(
                canonical_id="fr:perrache", name="Lyon Perrache", latitude=45.749,
                longitude=4.826,
            ), SimpleNamespace(service_score=4, source_stop_id="StopArea:perrache")),
        ]

        hubs = _rank_city_hubs(records, city)

        self.assertEqual([hub["name"] for hub in hubs[:2]], ["Lyon Part Dieu", "Lyon Perrache"])
        self.assertEqual(hubs[0]["source"], "sncf-gtfs")
        self.assertEqual(hubs[0]["gtfs_stop_ids"], {"fr-national-rail": "StopArea:OCE87723197"})


if __name__ == "__main__":
    unittest.main()
