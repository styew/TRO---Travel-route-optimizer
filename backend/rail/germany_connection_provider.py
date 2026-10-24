"""Connect German regional and long-distance timetable networks at canonical stations."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Awaitable, Callable, TypeVar
from zoneinfo import ZoneInfo

from backend.models.graph import TravelEdge
from backend.models.node import TransportNode
from backend.rail.journey_orchestrator import german_rail_journey_orchestrator
from backend.services.long_distance_train_provider import (
    LongDistanceTrainProviderUnavailableError,
    LongDistanceTrainRoutingError,
    OpenTripPlannerLongDistanceTrainProvider,
)
from backend.services.regional_train_provider import (
    OpenTripPlannerRegionalTrainProvider,
    RegionalTrainProviderUnavailableError,
    RegionalTrainRoutingError,
)

# The product can inspect ten physical handover stations, but OTP should not
# receive ten expensive routing requests at exactly the same instant.  These
# bounds keep one slow timetable query from blocking the complete search.
MAX_PARALLEL_TIMETABLE_QUERIES = 3
TIMETABLE_STAGE_TIMEOUT_SECONDS = 12
T = TypeVar("T")


def _scheduled_datetime(edge: TravelEdge, field: str) -> datetime | None:
    """Read a provider timestamp without letting malformed metadata break search."""
    value = edge.metadata.get(field)
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def _run_bounded_timetable_batch(
    items: list[T], operation: Callable[[T], Awaitable[list[TravelEdge]]]
) -> tuple[list[TravelEdge], int, list[str]]:
    """Run a bounded group of OTP queries and retain only responses on time.

    A timeout cancels pending work instead of holding the traveller's complete
    graph open.  The caller can still use the successful partial results and
    reports the omitted candidates through diagnostics.
    """
    semaphore = asyncio.Semaphore(MAX_PARALLEL_TIMETABLE_QUERIES)

    async def limited_operation(item: T) -> list[TravelEdge]:
        """Apply the per-stage concurrency budget to one candidate station or pair."""
        async with semaphore:
            return await operation(item)

    tasks = [asyncio.create_task(limited_operation(item)) for item in items]
    done, pending = await asyncio.wait(tasks, timeout=TIMETABLE_STAGE_TIMEOUT_SECONDS)
    results: list[TravelEdge] = []
    errors: list[str] = []
    try:
        for task in done:
            try:
                results.extend(task.result())
            except (RegionalTrainRoutingError, LongDistanceTrainRoutingError) as error:
                # One unsupported GTFS station must not discard routes found
                # through the remaining physical interchange candidates.
                errors.append(str(error))
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    return results, len(pending), errors


async def discover_german_network_edges(
    origin_nodes: list[TransportNode],
    destination_nodes: list[TransportNode],
    travel_date: str,
    departure_time: str,
    has_deutschlandticket: bool,
    travellers: int,
    minimum_train_transfer_minutes: int,
    search_entire_day: bool,
    gateway_departures: dict[str, datetime] | None = None,
) -> tuple[list[TransportNode], list[TravelEdge], list[dict[str, str]], dict]:
    """Build all domestic German rail edges through the modular network package.

    This is the single entry point used by the API for a German domestic
    journey. It keeps direct regional routes, direct paid long-distance routes,
    and regional-to-long-distance handovers in one bounded workflow. The
    former route-level provider functions remain low-level timetable adapters;
    the API no longer orchestrates them as independent legacy searches.
    """
    origins = [node for node in origin_nodes if node.type == "train_station"]
    destinations = [node for node in destination_nodes if node.type == "train_station"]
    if not origins or not destinations:
        return [], [], [], {
            "enabled": True,
            "reason": "A local origin and destination train station are required for German rail search.",
        }
    try:
        default_departure = datetime.fromisoformat(
            f"{travel_date}T{departure_time}:00"
        ).replace(tzinfo=ZoneInfo("Europe/Berlin"))
    except ValueError:
        return [], [], [{
            "infrastructure_type": "german_rail",
            "message": "The selected departure time is invalid.",
        }], {"enabled": True, "reason": "Invalid departure time."}

    station_pairs = [(origin, destination) for origin in origins for destination in destinations]
    regional_provider = OpenTripPlannerRegionalTrainProvider()
    long_distance_provider = OpenTripPlannerLongDistanceTrainProvider()
    warnings: list[dict[str, str]] = []
    regional_direct_edges: list[TravelEdge] = []
    long_distance_direct_edges: list[TravelEdge] = []
    regional_direct_timeouts = 0
    long_distance_direct_timeouts = 0
    regional_direct_errors: list[str] = []
    long_distance_direct_errors: list[str] = []

    async def direct_regional(pair: tuple[TransportNode, TransportNode]) -> list[TravelEdge]:
        """Resolve one direct D-Ticket station pair using the regional graph."""
        origin, destination = pair
        start = (gateway_departures or {}).get(origin.id, default_departure)
        if search_entire_day:
            start = start.replace(hour=0, minute=0)
        return await regional_provider.find_d_ticket_edges(
            origin, destination, travel_date, start,
            minimum_train_transfer_minutes, search_entire_day,
        )

    async def direct_long_distance(pair: tuple[TransportNode, TransportNode]) -> list[TravelEdge]:
        """Resolve one direct paid station pair using the long-distance graph."""
        origin, destination = pair
        start = (gateway_departures or {}).get(origin.id, default_departure)
        if search_entire_day:
            start = start.replace(hour=0, minute=0)
        return await long_distance_provider.find_estimated_fare_edges(
            origin, destination, travel_date, start,
            minimum_train_transfer_minutes, travellers, search_entire_day,
        )

    async def collect_direct_regional() -> tuple[list[TravelEdge], int, list[str], list[dict[str, str]]]:
        """Collect direct regional results without making paid rail wait for them."""
        if not has_deutschlandticket:
            return [], 0, [], []
        try:
            await regional_provider.ensure_available()
            edges, timeouts, errors = await _run_bounded_timetable_batch(
                station_pairs, direct_regional
            )
            return edges, timeouts, errors, []
        except RegionalTrainProviderUnavailableError:
            return [], 0, [], [{
                "infrastructure_type": "german_regional_trains",
                "message": "The regional timetable planner is unavailable; D-Ticket routes could not be checked.",
            }]

    async def collect_direct_long_distance() -> tuple[list[TravelEdge], int, list[str], list[dict[str, str]]]:
        """Collect direct paid results without making regional rail wait for them."""
        try:
            await long_distance_provider.ensure_available()
            edges, timeouts, errors = await _run_bounded_timetable_batch(
                station_pairs, direct_long_distance
            )
            return edges, timeouts, errors, []
        except LongDistanceTrainProviderUnavailableError:
            return [], 0, [], [{
                "infrastructure_type": "german_long_distance_trains",
                "message": "The long-distance timetable planner is unavailable; paid train schedules could not be checked.",
            }]

    (
        regional_direct_result,
        long_distance_direct_result,
        handover_result,
    ) = await asyncio.gather(
        collect_direct_regional(),
        collect_direct_long_distance(),
        discover_german_network_handover_edges(
            origin_nodes, destination_nodes, travel_date, departure_time,
            has_deutschlandticket, travellers, minimum_train_transfer_minutes,
            search_entire_day,
        ),
    )
    (
        regional_direct_edges,
        regional_direct_timeouts,
        regional_direct_errors,
        regional_warnings,
    ) = regional_direct_result
    (
        long_distance_direct_edges,
        long_distance_direct_timeouts,
        long_distance_direct_errors,
        long_distance_warnings,
    ) = long_distance_direct_result
    handover_nodes, handover_edges, handover_warnings, handover_diagnostics = handover_result
    warnings.extend(regional_warnings + long_distance_warnings)
    warnings.extend(handover_warnings)
    if regional_direct_timeouts or long_distance_direct_timeouts:
        warnings.append({
            "infrastructure_type": "german_rail",
            "message": "Some direct German rail timetable queries exceeded the time budget; available routes were kept.",
        })
    edges = list({edge.id: edge for edge in (
        regional_direct_edges + long_distance_direct_edges + handover_edges
    )}.values())
    diagnostics = {
        "enabled": True,
        "direct_station_pairs": len(station_pairs),
        "regional_direct_edge_count": len(regional_direct_edges),
        "long_distance_direct_edge_count": len(long_distance_direct_edges),
        "regional_direct_candidates_timed_out": regional_direct_timeouts,
        "long_distance_direct_candidates_timed_out": long_distance_direct_timeouts,
        "regional_direct_resolution_errors": len(regional_direct_errors),
        "long_distance_direct_resolution_errors": len(long_distance_direct_errors),
        "handover": handover_diagnostics,
    }
    return handover_nodes, edges, warnings, diagnostics


async def discover_german_network_handover_edges(
    origin_nodes: list[TransportNode],
    destination_nodes: list[TransportNode],
    travel_date: str,
    departure_time: str,
    has_deutschlandticket: bool,
    travellers: int,
    minimum_train_transfer_minutes: int,
    search_entire_day: bool,
) -> tuple[list[TransportNode], list[TravelEdge], list[dict[str, str]], dict]:
    """Discover timetable-valid D-Ticket→paid-train paths through real stations.

    The registry supplies no journey by itself.  It supplies a maximum of ten
    physical interchanges with exact stop IDs in both GTFS feeds.  The regional
    planner must first reach an interchange; only then does the long-distance
    planner receive a departure time after the scheduled handover buffer.
    """
    if not has_deutschlandticket:
        return [], [], [], {"enabled": False, "reason": "No Deutschlandticket selected."}
    origins = [node for node in origin_nodes if node.type == "train_station"]
    destinations = [node for node in destination_nodes if node.type == "train_station"]
    origin_center = next((node for node in origin_nodes if node.type == "city_center"), None)
    if not origins or not destinations or origin_center is None:
        return [], [], [], {
            "enabled": True,
            "reason": "A local origin and destination train station are required for the German handover search.",
        }
    try:
        requested_departure = datetime.fromisoformat(
            f"{travel_date}T{departure_time}:00"
        ).replace(tzinfo=ZoneInfo("Europe/Berlin"))
    except ValueError:
        return [], [], [{
            "infrastructure_type": "german_rail_handover",
            "message": "The selected departure time is invalid.",
        }], {"enabled": True, "reason": "Invalid departure time."}

    handover_nodes = german_rail_journey_orchestrator.create_handover_nodes(
        origin_center, limit=10
    )
    if not handover_nodes:
        return [], [], [{
            "infrastructure_type": "german_rail_handover",
            "message": "The canonical German station registry is empty. Run the registry build after updating GTFS data.",
        }], {"enabled": True, "reason": "Registry has no German interchanges."}

    regional_provider = OpenTripPlannerRegionalTrainProvider()
    regional_errors: list[str] = []

    async def reach_handover(handover: TransportNode) -> list[TravelEdge]:
        """Ask the regional graph whether one handover is reachable on this day."""
        results: list[TravelEdge] = []
        for origin in origins:
            try:
                start = requested_departure.replace(hour=0, minute=0) if search_entire_day else requested_departure
                results.extend(await regional_provider.find_d_ticket_edges(
                    origin, handover, travel_date, start,
                    minimum_train_transfer_minutes, search_entire_day,
                ))
            except RegionalTrainRoutingError as error:
                regional_errors.append(str(error))
            except RegionalTrainProviderUnavailableError:
                raise
        return results

    try:
        # Warm the feed identity before launching up to ten concurrent station
        # queries; otherwise every task would request the same metadata first.
        await regional_provider.ensure_available()
        access_edges, regional_timeout_count, batch_errors = await _run_bounded_timetable_batch(
            handover_nodes, reach_handover
        )
        regional_errors.extend(batch_errors)
    except RegionalTrainProviderUnavailableError:
        return handover_nodes, [], [{
            "infrastructure_type": "german_rail_handover",
            "message": "The regional timetable planner is unavailable; regional-to-long-distance handovers could not be checked.",
        }], {"enabled": True, "handover_candidates": len(handover_nodes), "reason": "Regional planner unavailable."}

    edges_by_handover: dict[str, list[TravelEdge]] = {node.id: [] for node in handover_nodes}
    for edge in access_edges:
        edges_by_handover.setdefault(edge.destination_node_id, []).append(edge)
    reachable_handover_nodes = [node for node in handover_nodes if edges_by_handover.get(node.id)]
    long_distance_provider = OpenTripPlannerLongDistanceTrainProvider()

    async def continue_from_handover(handover: TransportNode) -> list[TravelEdge]:
        """Query onward paid rail only after a scheduled regional arrival exists."""
        arrivals = [
            instant for edge in edges_by_handover[handover.id]
            if (instant := _scheduled_datetime(edge, "planned_arrival")) is not None
        ]
        if not arrivals:
            return []
        required_buffer = max(
            minimum_train_transfer_minutes,
            int(handover.metadata.get("minimum_transfer_minutes", 15)),
        )
        earliest_onward_departure = min(arrivals) + timedelta(minutes=required_buffer)
        if search_entire_day:
            earliest_onward_departure = earliest_onward_departure.replace(hour=0, minute=0)
        onward_edges: list[TravelEdge] = []
        for destination in destinations:
            try:
                onward_edges.extend(await long_distance_provider.find_estimated_fare_edges(
                    handover, destination, travel_date, earliest_onward_departure,
                    minimum_train_transfer_minutes, travellers, search_entire_day,
                ))
            except LongDistanceTrainRoutingError:
                continue
            except LongDistanceTrainProviderUnavailableError:
                raise
        for edge in onward_edges:
            edge.metadata["required_preceding_transfer_minutes"] = required_buffer
            edge.metadata["handover_station"] = handover.name
            edge.metadata["handover_type"] = "regional_to_long_distance"
        return onward_edges

    try:
        await long_distance_provider.ensure_available()
        onward_edges, long_distance_timeout_count, long_distance_errors = await _run_bounded_timetable_batch(
            reachable_handover_nodes, continue_from_handover
        )
    except LongDistanceTrainProviderUnavailableError:
        return handover_nodes, access_edges, [{
            "infrastructure_type": "german_rail_handover",
            "message": "Regional access was found, but the long-distance timetable planner is unavailable.",
        }], {"enabled": True, "handover_candidates": len(handover_nodes), "reachable_handover_candidates": len(reachable_handover_nodes), "reason": "Long-distance planner unavailable."}
    all_edges = list({edge.id: edge for edge in access_edges + onward_edges}.values())
    diagnostics = {
        "enabled": True,
        "handover_candidates": len(handover_nodes),
        "reachable_handover_candidates": len(reachable_handover_nodes),
        "regional_access_edge_count": len(access_edges),
        "long_distance_onward_edge_count": len(onward_edges),
        "regional_resolution_errors": len(regional_errors),
        "long_distance_resolution_errors": len(long_distance_errors),
        "regional_candidates_timed_out": regional_timeout_count,
        "long_distance_candidates_timed_out": long_distance_timeout_count,
    }
    warnings = []
    if regional_timeout_count or long_distance_timeout_count:
        warnings.append({
            "infrastructure_type": "german_rail_handover",
            "message": "Some German rail handover candidates exceeded the timetable time budget; available results were kept.",
        })
    return handover_nodes, all_edges, warnings, diagnostics
