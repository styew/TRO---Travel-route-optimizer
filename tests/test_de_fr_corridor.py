"""Exercise real GTFS calendars, crossing directions and composed transfers."""

import csv
import io
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.models.graph import GraphPolicy, RouteGraph, TravelEdge
from backend.models.node import TransportNode
from backend.rail.corridors.catalog import CorridorManifest, select_corridor
from backend.rail.corridors.provider import corridor_edges, discover_corridor_edges, _same_station
from backend.rail.corridors.timetable import build_timetable, open_timetable, service_instant
from backend.services.route_planner import find_lowest_price_routes


def station(key, name="Station", lat=49.0, lon=8.0):
    """Create a test physical station without implying an extra transfer."""
    return TransportNode(id=key, type="train_station", name=name, latitude=lat, longitude=lon,
                         supported_modes=["train"], source="test", source_id=key)


class CorridorTimetableTests(unittest.TestCase):
    """Use synthetic feeds so tests never depend on live operators or networks."""

    def setUp(self):
        """Create regional, high-speed, bus, foreign-detour and next-day services."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        source = self.path / "feed.zip"
        de, fr, ch = "StopArea:OCE80111111", "StopArea:OCE87222222", "StopArea:OCE85333333"
        self.de, self.fr = de, fr
        tables = {
            "stops.txt": (["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"], [
                [de, "Test DE", 49, 8, 1, ""], [fr, "Test FR", 48, 7, 1, ""], [ch, "Test CH", 47, 8, 1, ""],
                ["de-child", "Test DE", 49, 8, 0, de], ["fr-child", "Test FR", 48, 7, 0, fr],
            ]),
            "routes.txt": (["route_id", "route_type", "route_short_name", "route_long_name"], [
                ["regional", 2, "R", "Border regional"], ["fast", 101, "TGV", "Direct"], ["bus", 3, "BUS", "Bus"],
            ]),
            "trips.txt": (["route_id", "service_id", "trip_id"], [
                ["regional", "active", "regional"], ["fast", "active", "fast"], ["fast", "active", "reverse"],
                ["bus", "active", "bus"], ["regional", "removed", "cancelled"], ["fast", "active", "swiss"],
                ["regional", "previous", "overnight"], ["regional", "active", "no-pickup"],
            ]),
            "calendar.txt": (["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "start_date", "end_date"], [
                ["removed", 1, 1, 1, 1, 1, 1, 1, "20261029", "20261029"],
            ]),
            "calendar_dates.txt": (["service_id", "date", "exception_type"], [
                ["active", "20261029", 1], ["removed", "20261029", 2], ["previous", "20261028", 1],
            ]),
        }
        calls = []
        for trip in ("regional", "fast", "bus", "cancelled", "no-pickup"):
            calls.extend([[trip, "de-child", 1, "10:00:00", "10:00:00", 1 if trip == "no-pickup" else 0, 0],
                          [trip, "fr-child", 2, "11:00:00", "11:00:00", 0, 0]])
        calls.extend([["reverse", "fr-child", 1, "12:00:00", "12:00:00", 0, 0],
                      ["reverse", "de-child", 2, "13:00:00", "13:00:00", 0, 0],
                      ["overnight", "de-child", 1, "24:30:00", "24:30:00", 0, 0],
                      ["overnight", "fr-child", 2, "25:30:00", "25:30:00", 0, 0]])
        calls.extend([["swiss", parent, index, "14:00:00", "14:00:00", 0, 0] for index, parent in enumerate((de, ch, fr), 1)])
        tables["stop_times.txt"] = (["trip_id", "stop_id", "stop_sequence", "arrival_time", "departure_time", "pickup_type", "drop_off_type"], calls)
        with zipfile.ZipFile(source, "w") as archive:
            for name, (fields, rows) in tables.items():
                stream = io.StringIO()
                writer = csv.writer(stream)
                writer.writerow(fields)
                writer.writerows(rows)
                archive.writestr(name, stream.getvalue())
        target = self.path / "de-fr.sqlite"
        self.summary = build_timetable(source, target)
        self.manifest = CorridorManifest("de-fr", frozenset({"DE", "FR"}), str(target))
        self.db = open_timetable(str(target))
        self.addCleanup(self.db.close)
        self.origins = {de: station("de")}
        self.destinations = {fr: station("fr", lat=48, lon=7)}

    def edges(self, origin=None, destination=None, day="2026-10-29"):
        """Read the actual fixture timetable with passenger and transfer settings."""
        return corridor_edges(self.db, self.manifest, origin or self.origins,
                              destination or self.destinations, day, "00:00", 2, 20)

    def test_high_speed_and_regional_crossings_are_paid_with_exact_service_dates(self):
        """Neither a cancelled service nor an unrequested bus/detour becomes rail."""
        edges = self.edges()
        self.assertEqual({edge.metadata["gtfs_trip_id"] for edge in edges}, {"regional", "fast", "overnight"})
        self.assertTrue(all(edge.estimated_price_eur > 0 and not edge.metadata["deutschlandticket_eligible"] for edge in edges))
        self.assertEqual(self.edges(day="2026-10-30"), [])
        self.assertEqual(self.summary["stations"], 2)

    def test_reverse_direction_and_previous_day_gtfs_hours(self):
        """The timetable retains the direction and 24:xx timing of actual trips."""
        reverse = self.edges(self.destinations, self.origins)
        self.assertEqual([edge.metadata["gtfs_trip_id"] for edge in reverse], ["reverse"])
        night = next(edge for edge in self.edges() if edge.metadata["gtfs_trip_id"] == "overnight")
        self.assertEqual(night.metadata["planned_departure"], "2026-10-29T00:30:00+01:00")
        self.assertEqual(night.estimated_duration_minutes, 60)

    def test_dst_uses_gtfs_service_day_clock(self):
        """Noon minus twelve hours gives the correct winter-offset instant."""
        instant = service_instant(date(2026, 10, 25), 3 * 3600, "Europe/Paris")
        self.assertEqual(instant.isoformat(), "2026-10-25T03:00:00+01:00")

    def test_station_matching_requires_both_name_and_geography(self):
        """A nearby different terminal and a distant namesake are never joined."""
        node = station("one", "Test Hbf")
        self.assertTrue(_same_station(node, "Test Hauptbahnhof", 49, 8))
        self.assertFalse(_same_station(node, "Other terminal", 49, 8))
        self.assertFalse(_same_station(node, "Test Hbf", 48, 8))

    def test_three_leg_route_enforces_both_transfers_and_counts_waits(self):
        """DE regional → paid crossing → FR rail must meet both station buffers."""
        crossing = next(edge for edge in self.edges() if edge.metadata["gtfs_trip_id"] == "regional")
        access = TravelEdge(id="access", origin_node_id="start", destination_node_id="de", mode="train", edge_type="train_connection", estimated_distance_km=20, estimated_duration_minutes=30, estimated_price_eur=0, provider="test", metadata={"planned_departure": "2026-10-29T09:00:00+01:00", "planned_arrival": "2026-10-29T09:40:00+01:00"})
        egress = access.model_copy(deep=True, update={"id": "egress", "origin_node_id": "fr", "destination_node_id": "end", "estimated_price_eur": 15})
        egress.metadata.update(planned_departure="2026-10-29T11:20:00+01:00", planned_arrival="2026-10-29T12:00:00+01:00", required_preceding_transfer_minutes=20)
        graph = RouteGraph(nodes=[station(key) for key in ("start", "de", "fr", "end")], edges=[access, crossing, egress], origin_node_ids=["start"], destination_node_ids=["end"], policy=GraphPolicy())
        route = find_lowest_price_routes(graph, respect_departure_time=False).routes[0]
        self.assertEqual(route.transfer_count, 2)
        self.assertEqual(route.total_transfer_wait_minutes, 40)
        egress.metadata["planned_departure"] = "2026-10-29T11:19:00+01:00"
        self.assertEqual(find_lowest_price_routes(graph).routes, [])
        egress.metadata["planned_departure"] = "invalid"
        self.assertEqual(find_lowest_price_routes(graph).routes, [])


class CorridorCompositionTests(unittest.IsolatedAsyncioTestCase):
    """Verify API-facing composition without starting external routing engines."""

    async def test_direct_crossing_needs_no_domestic_handover_or_ticket(self):
        """The same real terminal can be the start/end of a direct international trip."""
        origin, destination = station("de"), station("fr")
        cross = TravelEdge(id="direct", origin_node_id="de", destination_node_id="fr", mode="train", edge_type="train_connection", estimated_distance_km=100, estimated_duration_minutes=90, estimated_price_eur=35, provider="test")
        for countries in (("DE", "FR"), ("FR", "DE")):
            manifest = select_corridor(*countries)
            self.assertIsNotNone(manifest)
            with patch("backend.rail.corridors.provider._load_crossings", return_value=({"a": origin}, {"b": destination}, [cross])), patch("backend.rail.corridors.provider.discover_german_network_edges", new_callable=AsyncMock) as german, patch("backend.rail.corridors.provider.discover_french_rail_edges", new_callable=AsyncMock) as french:
                _, edges, _, report = await discover_corridor_edges(manifest, [origin], [destination], *countries, "2026-10-29", "00:00", False, 1, 15, True)
            self.assertEqual(edges, [cross])
            self.assertEqual(report["crossing_edges"], 1)
            german.assert_not_awaited()
            french.assert_not_awaited()
        self.assertIsNone(select_corridor("DE", "DE"))
        self.assertIsNone(select_corridor("DE", "CH"))

    async def test_regional_access_is_priced_without_owned_ticket(self):
        """A regional schedule can be used without claiming a free D-Ticket leg."""
        origin, gateway, destination = station("origin"), station("gateway"), station("fr")
        access = TravelEdge(id="regional", origin_node_id="origin", destination_node_id="gateway", mode="train", edge_type="train_connection", estimated_distance_km=80, estimated_duration_minutes=60, estimated_price_eur=0, is_estimate=False, provider="test", metadata={"ticket_coverage": "Deutschlandticket", "price_status": "covered_by_existing_ticket"})
        crossing = access.model_copy(deep=True, update={"id": "crossing", "origin_node_id": "gateway", "destination_node_id": "fr", "estimated_price_eur": 35})
        crossing.metadata = {"planned_arrival": "2026-10-29T12:00:00+01:00"}
        with patch("backend.rail.corridors.provider._load_crossings", return_value=({"a": gateway}, {"b": destination}, [crossing])), patch("backend.rail.corridors.provider.discover_german_network_edges", new_callable=AsyncMock, return_value=([], [access], [], {})):
            _, edges, _, _ = await discover_corridor_edges(select_corridor("DE", "FR"), [origin], [destination], "DE", "FR", "2026-10-29", "00:00", False, 2, 20, True)
        regional = next(edge for edge in edges if edge.id == "regional")
        self.assertGreater(regional.estimated_price_eur, 0)
        self.assertTrue(regional.is_estimate)
        self.assertEqual(regional.metadata["ticket_coverage"], "estimated paid German regional fare")
        self.assertEqual(regional.metadata["required_preceding_transfer_minutes"], 20)

    async def test_search_endpoint_selects_both_directions_without_ticket(self):
        """The public search delegates DE–FR to the new package, with no Europe OTP."""
        from contextlib import ExitStack
        from backend.api.routes import search as search_module
        from backend.api.schemas.search import SearchRequest

        for origin_country, destination_country in (("DE", "FR"), ("FR", "DE")):
            cities = [dict(source_id=country, city_name=country, name=country, latitude=49.0, longitude=8.0, source="test", country_code=country) for country in (origin_country, destination_country)]
            infrastructure = [({"train_stations": [dict(id=country, name=country, latitude=49.0, longitude=8.0, source="test", distance_km=0)], "airports": []}, True, None, []) for country in (origin_country, destination_country)]
            with ExitStack() as stack:
                stack.enter_context(patch.object(search_module, "geocode", new_callable=AsyncMock, side_effect=cities))
                stack.enter_context(patch.object(search_module, "get_city_infrastructure", new_callable=AsyncMock, side_effect=infrastructure))
                for name in ("discover_flight_edges", "discover_commercial_train_edges", "discover_scheduled_flight_edges"):
                    stack.enter_context(patch.object(search_module, name, new_callable=AsyncMock, return_value=([], [])))
                corridor = stack.enter_context(patch.object(search_module, "discover_corridor_edges", new_callable=AsyncMock, return_value=([], [], [], {"enabled": True, "crossing_edges": 0})))
                result = await search_module.search(SearchRequest(origin="Test A", destination="Test B", date="2026-10-29", has_deutschlandticket=False, include_diagnostics=True, ranking_criterion="fastest"))
            self.assertEqual(corridor.await_args.args[0].key, "de-fr")
            self.assertEqual(corridor.await_args.args[3:5], (origin_country, destination_country))
            self.assertTrue(result["rail_diagnostics"]["international"]["enabled"])
            self.assertEqual(result["route_plan"].ranking_criterion, "fastest")
