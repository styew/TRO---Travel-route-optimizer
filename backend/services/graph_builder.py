"""Create the first safe car edges before timetable providers are connected."""

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from backend.models.graph import GraphPolicy, RouteGraph, TravelEdge
from backend.models.node import TransportNode


def _straight_line_distance_km(
    latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float
) -> float:
    """Calculate the geographic distance used as the base of a car-route estimate."""
    earth_radius_km = 6_371.0088
    latitude_delta = math.radians(latitude_b - latitude_a)
    longitude_delta = math.radians(longitude_b - longitude_a)
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(math.radians(latitude_a))
        * math.cos(math.radians(latitude_b))
        * math.sin(longitude_delta / 2) ** 2
    )
    return earth_radius_km * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))


def create_estimated_car_edge(
    origin: TransportNode,
    destination: TransportNode,
    edge_type: str,
    policy: GraphPolicy,
    departure_at: datetime | None = None,
) -> TravelEdge:
    """Estimate a car edge from coordinates until a routing provider is introduced."""
    straight_line_distance = _straight_line_distance_km(
        origin.latitude, origin.longitude, destination.latitude, destination.longitude
    )
    road_distance = straight_line_distance * policy.estimated_road_distance_factor
    duration_minutes = max(
        1, round((road_distance / policy.estimated_car_speed_kmh) * 60)
    )
    price_eur = round(
        (duration_minutes / 60) * policy.estimated_car_cost_per_hour_eur, 2
    )
    metadata = {
        "straight_line_distance_km": round(straight_line_distance, 2),
        "road_distance_factor": policy.estimated_road_distance_factor,
        "estimated_car_speed_kmh": policy.estimated_car_speed_kmh,
        "estimated_car_cost_per_hour_eur": policy.estimated_car_cost_per_hour_eur,
    }
    if departure_at:
        metadata["planned_departure"] = departure_at.isoformat()
        metadata["planned_arrival"] = (departure_at + timedelta(minutes=duration_minutes)).isoformat()
    return TravelEdge(
        id=f"car:{edge_type}:{origin.id}:{destination.id}",
        origin_node_id=origin.id,
        destination_node_id=destination.id,
        mode="car",
        edge_type=edge_type,
        estimated_distance_km=round(road_distance, 2),
        estimated_duration_minutes=duration_minutes,
        estimated_price_eur=price_eur,
        provider="TRO geometric car estimate",
        metadata=metadata,
    )


def gateway_train_departure_time(
    origin: TransportNode, gateway: TransportNode, travel_date: str, departure_time: str, policy: GraphPolicy
) -> datetime:
    """Return the earliest train-search time after driving to a gateway and changing modes."""
    journey_start = datetime.fromisoformat(f"{travel_date}T{departure_time}:00").replace(tzinfo=ZoneInfo("Europe/Berlin"))
    access_edge = create_estimated_car_edge(origin, gateway, "car_access_to_station", policy, journey_start)
    # Ten minutes gives the traveller time to park and reach the platform after driving.
    return journey_start + timedelta(minutes=access_edge.estimated_duration_minutes + 10)


def create_gateway_station_nodes(stations: list[dict]) -> list[TransportNode]:
    """Convert nearby main-station candidates into origin gateway nodes."""
    return [
        TransportNode(
            id=station["id"],
            type="train_station",
            name=station["name"],
            latitude=station["latitude"],
            longitude=station["longitude"],
            supported_modes=["train"],
            source=station["source"],
            source_id=station["id"],
            metadata={
                "graph_role": "origin_gateway_station",
                "distance_from_origin_center_km": station["distance_km"],
            },
        )
        for station in stations
    ]


def create_gateway_airport_nodes(airports: list[dict]) -> list[TransportNode]:
    """Convert bounded nearby flight candidates into car-accessible airport nodes."""
    return [
        TransportNode(
            id=airport["id"],
            type="airport",
            name=airport["name"],
            latitude=airport["latitude"],
            longitude=airport["longitude"],
            supported_modes=["flight"],
            source=airport["source"],
            source_id=airport["id"],
            metadata={
                "graph_role": "origin_gateway_airport",
                "distance_from_origin_center_km": airport["distance_km"],
                "iata": airport.get("iata"),
                "icao": airport.get("icao"),
            },
        )
        for airport in airports
    ]


def build_initial_route_graph(
    origin_nodes: list[TransportNode],
    destination_nodes: list[TransportNode],
    gateway_stations: list[TransportNode],
    gateway_airports: list[TransportNode] | None = None,
    additional_edges: list[TravelEdge] | None = None,
    policy: GraphPolicy | None = None,
    departure_at: datetime | None = None,
) -> RouteGraph:
    """Build car edges and append provider connections between known graph nodes."""
    policy = policy or GraphPolicy()
    origin_center = next(node for node in origin_nodes if node.type == "city_center")
    destination_center = next(
        node for node in destination_nodes if node.type == "city_center"
    )
    edges = [create_estimated_car_edge(origin_center, destination_center, "direct_car", policy, departure_at)]

    for gateway in gateway_stations:
        access_edge = create_estimated_car_edge(
            origin_center, gateway, "car_access_to_station", policy, departure_at
        )
        if access_edge.estimated_duration_minutes <= policy.max_car_access_minutes:
            edges.append(access_edge)

    for gateway in gateway_airports or []:
        access_edge = create_estimated_car_edge(
            origin_center, gateway, "car_access_to_airport", policy, departure_at
        )
        if access_edge.estimated_duration_minutes <= policy.max_airport_access_minutes:
            edges.append(access_edge)

    accepted_gateway_ids = {
        edge.destination_node_id
        for edge in edges
        if edge.edge_type == "car_access_to_station"
    }
    accepted_gateways = [
        station for station in gateway_stations if station.id in accepted_gateway_ids
    ][: policy.max_gateway_stations]
    accepted_gateway_airport_ids = {
        edge.destination_node_id
        for edge in edges
        if edge.edge_type == "car_access_to_airport"
    }
    accepted_gateway_airports = [
        airport for airport in gateway_airports or []
        if airport.id in accepted_gateway_airport_ids
    ]
    accepted_access_node_ids = {
        node.id for node in accepted_gateways + accepted_gateway_airports
    }
    accepted_edges = edges[:1] + [
        edge
        for edge in edges[1:]
        if edge.destination_node_id in accepted_access_node_ids
    ]
    known_node_ids = {
        node.id
        for node in origin_nodes + destination_nodes + accepted_gateways + accepted_gateway_airports
    }
    provider_edges = [
        edge
        for edge in additional_edges or []
        if edge.origin_node_id in known_node_ids and edge.destination_node_id in known_node_ids
    ]
    return RouteGraph(
        nodes=origin_nodes + destination_nodes + accepted_gateways + accepted_gateway_airports,
        edges=accepted_edges + provider_edges,
        origin_node_ids=[node.id for node in origin_nodes],
        destination_node_ids=[node.id for node in destination_nodes],
        policy=policy,
    )
