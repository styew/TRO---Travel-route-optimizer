"""Protect selection of fast and balanced routes before the top-ten cutoff."""

import unittest

from pydantic import ValidationError

from backend.api.schemas.search import SearchRequest
from backend.models.graph import GraphPolicy, RouteGraph, TravelEdge
from backend.models.node import TransportNode
from backend.services.route_planner import find_lowest_price_routes


class RouteRankingTests(unittest.TestCase):
    """Three distinct complete journeys should win the three priority modes."""

    def setUp(self):
        """Create a cheap slow, expensive fast and intermediate-price/time journey."""
        nodes = [TransportNode(id=key, type="train_station", name=key, latitude=49, longitude=8,
                               source="test", source_id=key, supported_modes=["train"])
                 for key in ("origin", "destination")]
        edges = [TravelEdge(id=key, origin_node_id="origin", destination_node_id="destination",
                            mode="train", edge_type="train_connection", provider="test",
                            estimated_distance_km=300, estimated_duration_minutes=duration,
                            estimated_price_eur=price)
                 for key, duration, price in (("cheap", 600, 100), ("fast", 100, 200), ("middle", 250, 120))]
        self.graph = RouteGraph(nodes=nodes, edges=edges, origin_node_ids=["origin"],
                                destination_node_ids=["destination"], policy=GraphPolicy())

    def test_priority_is_applied_before_limiting_results(self):
        """Fast and compromise options must survive even a one-result cutoff."""
        for criterion, expected in (("lowest_price", "cheap"), ("fastest", "fast"), ("balanced", "middle")):
            with self.subTest(criterion=criterion):
                plan = find_lowest_price_routes(self.graph, limit=1, ranking_criterion=criterion)
                self.assertEqual(plan.ranking_criterion, criterion)
                self.assertEqual(plan.routes[0].segments[0].id, expected)
                self.assertEqual(plan.routes[0].rank, 1)

    def test_balanced_handles_free_constant_and_empty_candidates(self):
        """Zero prices and equal dimensions must not divide by zero."""
        for edge in self.graph.edges:
            edge.estimated_price_eur = 0
        plan = find_lowest_price_routes(self.graph, ranking_criterion="balanced")
        self.assertEqual(plan.routes[0].segments[0].id, "fast")
        for edge in self.graph.edges:
            edge.estimated_duration_minutes = 100
        self.assertEqual(len(find_lowest_price_routes(self.graph, ranking_criterion="balanced").routes), 3)
        self.graph.edges = []
        self.assertEqual(find_lowest_price_routes(self.graph, ranking_criterion="balanced").routes, [])

    def test_request_defaults_remain_compatible_and_unknown_priority_is_rejected(self):
        """Existing API clients keep cheapest-first; misspelled modes fail validation."""
        values = dict(origin="Erfurt", destination="Paris", date="2026-10-29")
        self.assertEqual(SearchRequest(**values).ranking_criterion, "lowest_price")
        self.assertEqual(SearchRequest(**values, ranking_criterion="fastest").ranking_criterion, "fastest")
        with self.assertRaises(ValidationError):
            SearchRequest(**values, ranking_criterion="unknown")
