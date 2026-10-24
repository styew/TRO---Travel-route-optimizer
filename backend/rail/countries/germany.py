"""Germany's first modular rail-network manifest.

The manifest contains metadata only.  It does not load a GTFS archive when the
application starts; a registry build is an explicit offline maintenance task.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RailNetworkManifest:
    """Describe one independently routable timetable network."""

    key: str
    label: str
    archive_path: str
    fare_scope: str


GERMANY_REGIONAL_NETWORK = RailNetworkManifest(
    key="de-regional",
    label="Germany regional and Deutschlandticket services",
    archive_path=os.getenv(
        "REGIONAL_GTFS_ARCHIVE_PATH", "/app/otp-data/d-ticket/regional-d-ticket-gtfs.zip"
    ),
    fare_scope="deutschlandticket",
)

GERMANY_LONG_DISTANCE_NETWORK = RailNetworkManifest(
    key="de-long-distance",
    label="Germany long-distance and cross-border services",
    archive_path=os.getenv(
        "LONG_DISTANCE_GTFS_ARCHIVE_PATH", "/app/otp-data/long-distance/germany-long-distance-gtfs.zip"
    ),
    fare_scope="estimated_paid_fare",
)

GERMANY_NETWORKS = (GERMANY_REGIONAL_NETWORK, GERMANY_LONG_DISTANCE_NETWORK)
DEFAULT_SAME_STATION_TRANSFER_MINUTES = 15
