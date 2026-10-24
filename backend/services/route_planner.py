"""Rank complete graph paths by lowest estimated price."""

from collections import defaultdict
from datetime import datetime, timedelta

from backend.models.graph import RouteGraph, RouteOption, RoutePlan, TravelEdge


def _count_transport_categories(segments: list[TravelEdge]) -> int:
    """Count consecutive transport types once, so train-to-train is one category."""
    if not segments:
        return 0
    return 1 + sum(
        current.mode != previous.mode
        for previous, current in zip(segments, segments[1:])
    )


def _parse_schedule(value: object) -> datetime | None:
    """Parse a serialized provider timestamp without treating invalid text as time."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _retime_airport_access(
    segments: list[TravelEdge], airport_buffer_minutes: int
) -> list[TravelEdge]:
    """Plan the car leg backwards from a timed flight instead of adding idle waiting."""
    planned_segments = [segment.model_copy(deep=True) for segment in segments]
    for car_edge, flight_edge in zip(planned_segments, planned_segments[1:]):
        if (
            car_edge.edge_type != "car_access_to_airport"
            or flight_edge.mode != "flight"
        ):
            continue
        flight_departure = _parse_schedule(
            flight_edge.metadata.get("planned_departure")
        )
        if flight_departure is None:
            continue
        airport_arrival = flight_departure - timedelta(
            minutes=airport_buffer_minutes
        )
        car_departure = airport_arrival - timedelta(
            minutes=car_edge.estimated_duration_minutes
        )
        car_edge.metadata.update({
            "planned_departure": car_departure.isoformat(),
            "planned_arrival": airport_arrival.isoformat(),
            "airport_arrival_buffer_minutes": airport_buffer_minutes,
            "connection_timing": "latest_safe_departure_for_flight",
        })
    return planned_segments


def _create_route_option(
    rank: int, segments: list[TravelEdge], airport_buffer_minutes: int
) -> RouteOption:
    """Aggregate one route after calculating its latest safe airport arrival plan."""
    segments = _retime_airport_access(segments, airport_buffer_minutes)
    category_count = _count_transport_categories(segments)
    internal_transfers = sum(
        int(edge.metadata.get("transfer_count", 0)) for edge in segments
    )
    departure = segments[0].metadata.get("planned_departure")
    arrival = segments[-1].metadata.get("planned_arrival")
    segment_duration_minutes = sum(
        edge.estimated_duration_minutes for edge in segments
    )
    scheduled_departure = _parse_schedule(departure)
    scheduled_arrival = _parse_schedule(arrival)
    elapsed_duration_minutes = 0
    if scheduled_departure and scheduled_arrival:
        elapsed_duration_minutes = max(
            0,
            round((scheduled_arrival - scheduled_departure).total_seconds() / 60),
        )
    return RouteOption(
        rank=rank,
        node_path=[segments[0].origin_node_id] + [
            edge.destination_node_id for edge in segments
        ],
        segments=segments,
        total_estimated_price_eur=round(
            sum(edge.estimated_price_eur for edge in segments), 2
        ),
        total_estimated_duration_minutes=max(
            segment_duration_minutes, elapsed_duration_minutes
        ),
        total_estimated_distance_km=round(
            sum(edge.estimated_distance_km for edge in segments), 2
        ),
        planned_departure=departure,
        planned_arrival=arrival,
        total_transfer_wait_minutes=sum(int(edge.metadata.get("total_transfer_wait_minutes", 0)) for edge in segments),
        transfer_count=internal_transfers + max(0, category_count - 1),
        transport_category_count=category_count,
        transport_category_changes=max(0, category_count - 1),
    )


def find_lowest_price_routes(
    graph: RouteGraph, limit: int = 10, respect_departure_time: bool = True
) -> RoutePlan:
    """Return cheap paths while always preserving chronology between segments.

    ``respect_departure_time`` controls whether provider searches begin at the
    traveller's chosen time. It must never permit an impossible connection
    where the first train reaches a station after the next train has left.
    """
    outgoing_edges: dict[str, list[TravelEdge]] = defaultdict(list)
    for edge in graph.edges:
        outgoing_edges[edge.origin_node_id].append(edge)

    completed_paths: list[list[TravelEdge]] = []
    destination_ids = set(graph.destination_node_ids)

    def has_time_conflict(previous: TravelEdge, next_edge: TravelEdge) -> bool:
        """Reject unreachable timed connections, including airport arrival margin."""
        previous_arrival = previous.metadata.get("planned_arrival")
        next_departure = next_edge.metadata.get("planned_departure")
        # A cached price without a departure timestamp is useful as a reference,
        # but cannot safely be joined to a preceding car journey.
        if previous.mode == "car" and next_edge.mode == "flight" and not next_departure:
            return True
        if not previous_arrival or not next_departure:
            return False
        try:
            required_arrival = datetime.fromisoformat(previous_arrival)
            # A D-Ticket leg and an international paid ticket meet at the
            # same transport mode, but still need a deliberate station buffer.
            # This is a fare-boundary transfer, not another transport category.
            required_arrival += timedelta(
                minutes=int(next_edge.metadata.get("required_preceding_transfer_minutes", 0))
            )
            if previous.mode == "car" and next_edge.mode == "flight":
                required_arrival += timedelta(
                    minutes=graph.policy.airport_arrival_buffer_minutes
                )
            return datetime.fromisoformat(next_departure) < required_arrival
        except ValueError:
            return False

    def explore(node_id: str, segments: list[TravelEdge], visited_nodes: set[str]) -> None:
        """Expand paths without cycles, excess category changes, or runaway edge hops."""
        if node_id in destination_ids and segments:
            completed_paths.append(segments)
            return
        if len(segments) == graph.policy.max_edge_hops:
            return
        for edge in outgoing_edges[node_id]:
            if edge.destination_node_id in visited_nodes:
                continue
            if segments and has_time_conflict(segments[-1], edge):
                continue
            next_segments = segments + [edge]
            if _count_transport_categories(next_segments) > graph.policy.max_transport_categories:
                continue
            explore(
                edge.destination_node_id,
                next_segments,
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
        max_transport_categories=graph.policy.max_transport_categories,
        routes=[
            _create_route_option(
                rank, path, graph.policy.airport_arrival_buffer_minutes
            )
            for rank, path in enumerate(sorted_paths, start=1)
        ],
    )
