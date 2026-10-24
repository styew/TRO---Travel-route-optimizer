"""Regression tests for D-Ticket handover into an international rail corridor."""

import unittest

from backend.models.graph import GraphPolicy, RouteGraph, TravelEdge
from backend.models.node import TransportNode
from backend.services.international_rail_provider import _itinerary_distance_km, _provisional_price
from backend.services.regional_train_provider import OpenTripPlannerRegionalTrainProvider
from backend.services.rail_network_catalog import (
    corridor_destination_station_locations,
    corridor_destination_station_names,
    corridor_gateway_nodes,
    select_rail_corridor,
)
from backend.services.route_planner import find_lowest_price_routes


def _station(identifier: str, name: str) -> TransportNode:
    """Create a minimal rail node used only to exercise route graph behaviour."""
    return TransportNode(
        id=identifier,
        type="train_station",
        name=name,
        latitude=49.0,
        longitude=8.0,
        supported_modes=["train"],
        source="test",
        source_id=identifier,
    )


class InternationalRailPlanningTests(unittest.TestCase):
    """Protect the bounded-corridor and fare-boundary rules from regressions."""

    def test_catalog_selects_only_configured_germany_france_corridor(self) -> None:
        """A Germany-only search has no corridor or hard-coded gateway list."""
        corridor = select_rail_corridor("DE", "FR")
        self.assertIsNotNone(corridor)
        self.assertEqual(corridor.key, "de-fr-rail")
        self.assertFalse(hasattr(corridor, "gateways"))
        self.assertLessEqual(len(corridor_gateway_nodes(corridor)), corridor.max_gateway_candidates)
        self.assertIsNone(select_rail_corridor("DE", "DE"))

    def test_catalog_uses_a_real_paris_intercity_terminal(self) -> None:
        """Paris must not resolve only to the closest regional-only terminal."""
        corridor = select_rail_corridor("DE", "FR")
        paris = TransportNode(
            id="paris", type="city_center", name="Paris", latitude=48.8589,
            longitude=2.3200, supported_modes=["car"], source="test", source_id="paris",
        )
        terminals = corridor_destination_station_names(corridor, paris)
        self.assertIn("Paris Est", terminals)
        locations = corridor_destination_station_locations(corridor, paris)
        paris_est = next(location for location in locations if location["name"] == "Paris Est")
        self.assertEqual(paris_est["stop_location_id"], "0:StopPoint:OCEICE-87113001")

    def test_paid_train_requires_gateway_transfer_buffer(self) -> None:
        """A paid international departure ten minutes after arrival is rejected."""
        origin = _station("origin", "Erfurt Hbf")
        gateway = _station("gateway", "Mannheim Hbf")
        destination = _station("destination", "Paris Gare de Lyon")
        regional = TravelEdge(
            id="d-ticket-access",
            origin_node_id=origin.id,
            destination_node_id=gateway.id,
            mode="train",
            edge_type="train_connection",
            estimated_distance_km=220,
            estimated_duration_minutes=120,
            estimated_price_eur=0,
            is_estimate=False,
            provider="test",
            metadata={"planned_arrival": "2026-10-28T10:00:00+01:00"},
        )
        paid = TravelEdge(
            id="paid-international",
            origin_node_id=gateway.id,
            destination_node_id=destination.id,
            mode="train",
            edge_type="train_connection",
            estimated_distance_km=450,
            estimated_duration_minutes=180,
            estimated_price_eur=92,
            provider="test",
            metadata={
                "planned_departure": "2026-10-28T10:10:00+01:00",
                "required_preceding_transfer_minutes": 15,
            },
        )
        graph = RouteGraph(
            nodes=[origin, gateway, destination],
            edges=[regional, paid],
            origin_node_ids=[origin.id],
            destination_node_ids=[destination.id],
            policy=GraphPolicy(),
        )
        self.assertEqual(find_lowest_price_routes(graph).routes, [])

    def test_flexible_start_time_still_rejects_a_train_that_already_left(self) -> None:
        """Whole-day comparison relaxes the start time, not train chronology."""
        origin, gateway, destination = _station("origin", "Erfurt Hbf"), _station("gateway", "Mannheim Hbf"), _station("destination", "Paris Est")
        regional = TravelEdge(
            id="regional", origin_node_id=origin.id, destination_node_id=gateway.id,
            mode="train", edge_type="train_connection", estimated_distance_km=200,
            estimated_duration_minutes=240, estimated_price_eur=0, provider="test",
            metadata={"planned_departure": "2026-10-29T10:00:00+01:00", "planned_arrival": "2026-10-29T14:00:00+01:00"},
        )
        paid = TravelEdge(
            id="paid", origin_node_id=gateway.id, destination_node_id=destination.id,
            mode="train", edge_type="train_connection", estimated_distance_km=450,
            estimated_duration_minutes=180, estimated_price_eur=90, provider="test",
            metadata={"planned_departure": "2026-10-29T13:42:00+01:00", "planned_arrival": "2026-10-29T16:50:00+01:00", "required_preceding_transfer_minutes": 15},
        )
        graph = RouteGraph(nodes=[origin, gateway, destination], edges=[regional, paid], origin_node_ids=[origin.id], destination_node_ids=[destination.id], policy=GraphPolicy())
        self.assertEqual(find_lowest_price_routes(graph, respect_departure_time=False).routes, [])

    def test_paid_train_after_buffer_forms_one_train_category(self) -> None:
        """D-Ticket and paid rail stay one transport category but sum their cost."""
        origin = _station("origin", "Erfurt Hbf")
        gateway = _station("gateway", "Mannheim Hbf")
        destination = _station("destination", "Paris Gare de Lyon")
        regional = TravelEdge(
            id="d-ticket-access", origin_node_id=origin.id, destination_node_id=gateway.id,
            mode="train", edge_type="train_connection", estimated_distance_km=220,
            estimated_duration_minutes=120, estimated_price_eur=0, is_estimate=False,
            provider="test", metadata={"planned_departure": "2026-10-28T08:00:00+01:00", "planned_arrival": "2026-10-28T10:00:00+01:00"},
        )
        paid = TravelEdge(
            id="paid-international", origin_node_id=gateway.id, destination_node_id=destination.id,
            mode="train", edge_type="train_connection", estimated_distance_km=450,
            estimated_duration_minutes=180, estimated_price_eur=92, provider="test",
            metadata={"planned_departure": "2026-10-28T10:20:00+01:00", "planned_arrival": "2026-10-28T13:20:00+01:00", "required_preceding_transfer_minutes": 15},
        )
        graph = RouteGraph(nodes=[origin, gateway, destination], edges=[regional, paid], origin_node_ids=[origin.id], destination_node_ids=[destination.id], policy=GraphPolicy())
        route = find_lowest_price_routes(graph).routes[0]
        self.assertEqual(route.total_estimated_price_eur, 92)
        self.assertEqual(route.transport_category_count, 1)

    def test_coordinate_fallback_prevents_zero_distance_price_lookup(self) -> None:
        """A geometry-free OTP rail leg still receives a usable distance band."""
        distance, source = _itinerary_distance_km([{
            "distance": None,
            "from": {"lat": 49.4794, "lon": 8.4696},
            "to": {"lat": 48.876, "lon": 2.359},
        }])
        self.assertGreater(distance, 400)
        self.assertEqual(source, "coordinate_estimate")

    def test_unprofiled_international_schedule_receives_visible_fallback_price(self) -> None:
        """Missing price research must not delete a valid timetable edge."""
        estimate = _provisional_price(distance_km=450, passengers=1)
        self.assertEqual(estimate["price_status"], "provisional_distance_estimate")
        self.assertGreater(float(estimate["typical_eur"]), 0)
        self.assertGreater(float(estimate["maximum_eur"]), float(estimate["typical_eur"]))

    def test_d_ticket_filter_rejects_leo_express_and_night_train_operators(self) -> None:
        """A filtered input archive must not turn paid operators into free travel."""
        for operator in ("LEO Express", "ÖBB-Personenverkehr AG"):
            self.assertFalse(OpenTripPlannerRegionalTrainProvider._is_d_ticket_eligible_leg({
                "route": {"shortName": "DRF", "agency": {"name": operator}},
            }))


if __name__ == "__main__":
    unittest.main()
