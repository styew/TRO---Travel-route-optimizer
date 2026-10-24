"""Rank complete graph paths by price, total journey time or their balance."""

from collections import defaultdict
from datetime import datetime, timedelta

from backend.models.graph import RouteGraph, RouteOption, RoutePlan, RouteRanking, TravelEdge


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
    # Two train edges still require a real train change, even though they
    # count as one transport category in the user's modal-change limit.
    rail_joins = [
        (previous, following) for previous, following in zip(segments, segments[1:])
        if previous.mode == following.mode == "train"
    ]
    join_wait_minutes = 0
    for previous, following in rail_joins:
        previous_arrival = _parse_schedule(previous.metadata.get("planned_arrival"))
        following_departure = _parse_schedule(following.metadata.get("planned_departure"))
        if previous_arrival and following_departure:
            join_wait_minutes += max(0, round((following_departure - previous_arrival).total_seconds() / 60))
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
        total_transfer_wait_minutes=join_wait_minutes + sum(int(edge.metadata.get("total_transfer_wait_minutes", 0)) for edge in segments),
        transfer_count=internal_transfers + len(rail_joins) + max(0, category_count - 1),
        transport_category_count=category_count,
        transport_category_changes=max(0, category_count - 1),
    )


def find_lowest_price_routes(
    graph: RouteGraph, limit: int = 10, respect_departure_time: bool = True,
    ranking_criterion: RouteRanking = "lowest_price",
) -> RoutePlan:
    """Rank every valid complete path by price, elapsed time or their balance.

    The historical function name remains for existing callers. Balanced mode
    gives equal weight to price and elapsed-time ranges across all candidates:
    0.5 * (price - min_price) / price_range +
    0.5 * (duration - min_duration) / duration_range.
    A constant dimension contributes zero. Ranking precedes the result limit.
    ``respect_departure_time`` controls whether provider searches begin at the
    traveller's chosen time. It must never permit an impossible connection
    where the first train reaches a station after the next train has left.
    """
    if ranking_criterion not in {"lowest_price", "fastest", "balanced"}:
        raise ValueError("Unknown route ranking criterion.")
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
            if previous.mode == next_edge.mode == "train":
                return True
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
        except (ValueError, TypeError):
            return previous.mode == next_edge.mode == "train"

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

    candidates = [
        _create_route_option(0, path, graph.policy.airport_arrival_buffer_minutes)
        for path in completed_paths
    ]
    min_price = min((route.total_estimated_price_eur for route in candidates), default=0)
    min_duration = min((route.total_estimated_duration_minutes for route in candidates), default=0)
    price_range = max((route.total_estimated_price_eur for route in candidates), default=0) - min_price
    duration_range = max((route.total_estimated_duration_minutes for route in candidates), default=0) - min_duration

    def ranking_key(route: RouteOption) -> tuple:
        """Apply the selected priority with deterministic, schedule-aware ties."""
        price, duration = route.total_estimated_price_eur, route.total_estimated_duration_minutes
        if ranking_criterion == "fastest":
            priority = (duration, price)
        elif ranking_criterion == "balanced":
            score = 0.5 * ((price - min_price) / price_range if price_range else 0)
            score += 0.5 * ((duration - min_duration) / duration_range if duration_range else 0)
            priority = (score, duration, price)
        else:
            priority = (price, duration)
        return (*priority, len(route.segments), tuple(edge.id for edge in route.segments))

    ranked = sorted(candidates, key=ranking_key)[:limit]
    for rank, route in enumerate(ranked, start=1):
        route.rank = rank
    return RoutePlan(
        ranking_criterion=ranking_criterion,
        max_transport_categories=graph.policy.max_transport_categories,
        routes=ranked,
    )
