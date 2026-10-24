# TRO architecture

## Runtime path

1. The frontend submits an origin, destination, date and transport filters.
2. The FastAPI search route geocodes both cities and reads cached terminal
   candidates from SQLite when available.
3. On a cache miss, the infrastructure services discover city centres, main
   stations and airports, then save only approved candidates.
4. Providers independently add car estimates, regional Deutschlandticket
   schedules, German long-distance schedules, bounded international rail
   corridors and flight references.
5. The graph builder combines nodes and edges. The route planner returns up to
   ten cycle-free paths ranked by estimated price.

## Data boundaries

* `data/tro.db` stores TRO-owned cache records, imported schedules and price
  observations. It does not store complete national timetables.
* `otp-data/d-ticket/` holds the Germany regional graph.
* `otp-data/long-distance/` holds the German paid long-distance graph.
* `otp-data/europe/raw/` holds downloaded source archives temporarily.
* `otp-data/europe/rail/` holds rail-only archives and optional corridor
  graphs. They must be rebuilt whenever their source feeds change.
* `backend/services/rail_network_catalog.py` is metadata only: it maps a
  country pair to one optional graph and a bounded list of gateway stations.
  It prevents a Germany-only search from loading or querying all of Europe.

## Script groups

* `scripts/rail/` downloads, filters and calibrates railway data.
* `scripts/flights/` imports and analyses published flight schedules.
* `scripts/database/` provides read-only database inspection.

## Important status

The German graphs are used by the backend today. The first optional corridor
is `DE-FR`: a traveller with a Deutschlandticket can reach Mannheim or
Karlsruhe on regional services, then continue on a separately
priced international train to a French city. The international segment is an
estimate from `rail_price_profiles`, never a live ticket offer. The Europe
profile must be started explicitly; when it is off, domestic searches still
run normally and the API returns a clear warning.
