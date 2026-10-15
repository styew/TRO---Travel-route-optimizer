"""Boundary for paid rail providers such as Omio.

The provider is deliberately server-side because partner credentials and affiliate
tracking identifiers must never be delivered to a browser.
"""

import os
from datetime import datetime

from backend.models.graph import TravelEdge
from backend.models.node import TransportNode


class CommercialTrainProvider:
    """Contract used by route discovery for priced train itineraries."""

    async def find_edges(
        self, origin: TransportNode, destination: TransportNode, departure_at: datetime
    ) -> list[TravelEdge]:
        """Return bookable train connections with schedules, price, and booking URL."""
        raise NotImplementedError


class OmioTrainProvider(CommercialTrainProvider):
    """Reserved integration point for approved Omio partner API credentials.

    Omio's concrete request contract is provided only to approved partners. This
    class therefore refuses live requests until the project has a documented
    partner endpoint and credential, instead of guessing an unsupported API.
    """

    def __init__(self) -> None:
        """Load only the presence of partner configuration, never expose its value."""
        self._api_key = os.getenv("OMIO_API_KEY")
        self._api_url = os.getenv("OMIO_API_URL")

    @property
    def is_configured(self) -> bool:
        """Return whether the approved partner connection has been configured."""
        return bool(self._api_key and self._api_url)

    async def find_edges(
        self, origin: TransportNode, destination: TransportNode, departure_at: datetime
    ) -> list[TravelEdge]:
        """Reserve real calls until the approved Omio schema is available."""
        if not self.is_configured:
            return []
        raise NotImplementedError(
            "Omio credentials are configured, but the approved partner request schema has not been added yet."
        )


async def discover_commercial_train_edges(
    origin_nodes: list[TransportNode], destination_nodes: list[TransportNode], travel_date: str,
    departure_time: str, has_deutschlandticket: bool, gateway_departures: dict[str, datetime] | None = None,
) -> tuple[list[TravelEdge], list[dict[str, str]]]:
    """Prepare paid-train discovery while making missing partner access explicit.

    D-Ticket and commercial routes will later be composed through timed interchange
    stations. No speculative request is sent to an undocumented Omio endpoint.
    """
    provider = OmioTrainProvider()
    if not provider.is_configured:
        return [], [{
            "infrastructure_type": "commercial_trains",
            "message": "Paid train search is ready for an approved Omio partner API, but no Omio partner credentials are configured yet.",
        }]
    return [], [{
        "infrastructure_type": "commercial_trains",
        "message": "Omio partner credentials were found, but its approved request schema still needs to be configured before live searches can run.",
    }]
