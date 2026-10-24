"""Build only the Germany–France corridor; no OTP/Europe graph is required."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.rail.corridors.timetable import build_timetable


def main() -> None:
    """Accept explicit paths for repeatable local and Docker maintenance runs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("otp-data/france/france-sncf-gtfs.zip"))
    parser.add_argument("--output", type=Path, default=Path("data/rail/de-fr.sqlite"))
    args = parser.parse_args()
    print(json.dumps(build_timetable(args.source, args.output), indent=2))


if __name__ == "__main__":
    main()
