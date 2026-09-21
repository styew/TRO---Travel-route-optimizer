"""Build graph nodes from normalized city infrastructure candidates."""

from backend.models.node import TransportNode


def create_city_transport_nodes(
    city: dict,
    infrastructure: dict,
) -> list[TransportNode]:
    """Create the long-distance city, rail, and airport nodes for one city."""
    nodes = [
        TransportNode(
            id=f"city_center:{city['source_id']}",
            type="city_center",
            name=city["city_name"],
            latitude=city["latitude"],
            longitude=city["longitude"],
            supported_modes=["car"],
            source=city["source"],
            source_id=city["source_id"],
            metadata={"display_name": city["name"]},
        )
    ]

    for station in infrastructure["train_stations"]:
        nodes.append(
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
                    "distance_from_city_center_km": station["distance_km"],
                },
            )
        )

    for airport in infrastructure["airports"]:
        nodes.append(
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
                    "distance_from_city_center_km": airport["distance_km"],
                    "iata": airport["iata"],
                    "icao": airport["icao"],
                },
            )
        )

    return nodes
