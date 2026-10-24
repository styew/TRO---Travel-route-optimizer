"""First bounded selection stage for the modular German rail journey planner."""

from __future__ import annotations

from backend.database.session import SessionLocal
from backend.models.node import TransportNode
from backend.rail.station_registry import InterchangeCandidate, nearest_interchanges


class GermanRailJourneyOrchestrator:
    """Select canonical transfer stations before querying separate OTP networks.

    It deliberately does not query every German station.  It returns at most
    ten candidate handovers; the next routing stage will ask the regional and
    long-distance timetable engines which candidates are reachable at the
    requested time.  This separates station identity from route discovery.
    """

    def select_long_distance_handover_candidates(
        self, origin: TransportNode, limit: int = 10
    ) -> list[InterchangeCandidate]:
        """Find nearby regional→long-distance station handovers for one origin node."""
        with SessionLocal() as session:
            return nearest_interchanges(session, origin.latitude, origin.longitude, limit=limit)

    def create_handover_nodes(
        self, origin: TransportNode, limit: int = 10
    ) -> list[TransportNode]:
        """Create graph nodes that retain exact IDs for both German GTFS feeds."""
        return [
            TransportNode(
                id=f"rail-interchange:{candidate.canonical_station_id}",
                type="train_station",
                name=candidate.name,
                latitude=candidate.latitude,
                longitude=candidate.longitude,
                supported_modes=["train"],
                source="TRO canonical German station registry",
                source_id=candidate.canonical_station_id,
                metadata={
                    "graph_role": "german_regional_to_long_distance_interchange",
                    "distance_from_origin_center_km": candidate.distance_km,
                    "minimum_transfer_minutes": candidate.minimum_transfer_minutes,
                    "gtfs_stop_ids": {
                        "de-regional": candidate.from_stop_id,
                        "de-long-distance": candidate.to_stop_id,
                    },
                },
            )
            for candidate in self.select_long_distance_handover_candidates(origin, limit)
        ]


german_rail_journey_orchestrator = GermanRailJourneyOrchestrator()
