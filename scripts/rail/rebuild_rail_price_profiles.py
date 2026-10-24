"""Rebuild transparent statistical fare profiles from classified observations."""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.database.session import initialize_database
from backend.services.rail_price_estimator import rail_price_profile_repository


if __name__ == "__main__":
    initialize_database()
    print(
        "Rebuilt "
        f"{rail_price_profile_repository.rebuild_derived_profiles()} statistical rail-price profile(s)."
    )
