"""France's modular rail-network manifest.

The French network is independent from Germany: it can be built, started and
updated without loading Switzerland or future countries into the same OTP
process. Cross-border services will be introduced later through explicit
corridor records rather than by merging country graphs.
"""

from __future__ import annotations

import os

from backend.rail.countries.germany import RailNetworkManifest


FRANCE_RAIL_NETWORK = RailNetworkManifest(
    key="fr-national-rail",
    label="France SNCF national rail services",
    archive_path=os.getenv(
        "FRANCE_GTFS_ARCHIVE_PATH", "/app/otp-data/france/france-sncf-gtfs.zip"
    ),
    fare_scope="estimated_paid_fare",
)

FRANCE_NETWORKS = (FRANCE_RAIL_NETWORK,)
