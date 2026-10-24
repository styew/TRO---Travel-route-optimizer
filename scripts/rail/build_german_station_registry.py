"""Build the canonical German station registry from the two installed GTFS feeds.

Run inside the backend container after updating a GTFS archive:
    python scripts/rail/build_german_station_registry.py

The script writes only TRO's SQLite database.  It never modifies the original
GTFS ZIP files or the OpenTripPlanner graph files.
"""

import sys
from pathlib import Path

# Direct script execution starts with ``scripts/rail`` on Python's module path.
# Add the project root so ``backend`` imports work both locally and in Docker.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.database.session import SessionLocal, initialize_database
from backend.rail.station_registry import build_germany_station_registry


def main() -> None:
    """Initialize database tables and import the current German station mappings."""
    initialize_database()
    with SessionLocal() as session:
        summary = build_germany_station_registry(session)
    print(
        "German rail registry built: "
        f"{summary.stations} canonical stations, {summary.memberships} memberships, "
        f"{summary.interchanges} directed interchanges."
    )


if __name__ == "__main__":
    main()
