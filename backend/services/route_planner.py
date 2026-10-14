"""Rank complete graph paths by lowest estimated price."""

from collections import defaultdict

from backend.models.graph import RouteGraph, RouteOption, RoutePlan, TravelEdge


def _create_route_option(rank: int, segments: list[TravelEdge]) -> RouteOption:
    """Aggregate segment estimates into one route option returned to the client."""
    return RouteOption(
        rank=rank,
        node_path=[segments[0].origin_node_id] + [
            edge.destination_node_id for edge in segments
        ],
        segments=segments,
        total_estimated_price_eur=round(
            sum(edge.estimated_price_eur for edge in segments), 2
        ),
        total_estimated_duration_minutes=sum(
            edge.estimated_duration_minutes for edge in segments
        ),
        total_estimated_distance_km=round(
            sum(edge.estimated_distance_km for edge in segments), 2
        ),
        transfer_count=max(0, len(segments) - 1),
    )


def find_lowest_price_routes(graph: RouteGraph, limit: int = 10) -> RoutePlan:
    """Return up to ten cheapest cycle-free paths that respect the segment limit."""
    outgoing_edges: dict[str, list[TravelEdge]] = defaultdict(list)
    for edge in graph.edges:
        outgoing_edges[edge.origin_node_id].append(edge)

    completed_paths: list[list[TravelEdge]] = []
    destination_ids = set(graph.destination_node_ids)

    def explore(node_id: str, segments: list[TravelEdge], visited_nodes: set[str]) -> None:
        """Expand an unfinished path without revisiting nodes or exceeding policy limits."""
        if node_id in destination_ids and segments:
            completed_paths.append(segments)
            return
        if len(segments) == graph.policy.max_segments:
            return
        for edge in outgoing_edges[node_id]:
            if edge.destination_node_id in visited_nodes:
                continue
            explore(
                edge.destination_node_id,
                segments + [edge],
                visited_nodes | {edge.destination_node_id},
            )

    for origin_id in graph.origin_node_ids:
        explore(origin_id, [], {origin_id})

    sorted_paths = sorted(
        completed_paths,
        key=lambda path: (
            sum(edge.estimated_price_eur for edge in path),
            sum(edge.estimated_duration_minutes for edge in path),
            len(path),
            tuple(edge.id for edge in path),
        ),
    )[:limit]
    return RoutePlan(
        max_segments=graph.policy.max_segments,
        routes=[
            _create_route_option(rank, path)
            for rank, path in enumerate(sorted_paths, start=1)
        ],
    )
