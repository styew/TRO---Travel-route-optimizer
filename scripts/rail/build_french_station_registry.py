"""Build the French city-hub index from the installed SNCF GTFS archive.

Run after replacing ``otp-data/france/france-sncf-gtfs.zip``.  The command
writes only TRO's SQLite index; it does not edit the GTFS file or OTP graph.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.database.session import SessionLocal, initialize_database
from backend.rail.france_station_registry import build_france_station_registry


def main() -> None:
    """Create database tables if needed and persist the current SNCF hubs."""
    initialize_database()
    with SessionLocal() as session:
        summary = build_france_station_registry(session)
    print(
        "French rail hub registry built: "
        f"{summary.stations} canonical stations, {summary.memberships} memberships."
    )


if __name__ == "__main__":
    main()
