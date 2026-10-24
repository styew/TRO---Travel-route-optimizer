# International rail: Germany ↔ France

## Implemented composition

The search API selects `backend/rail/corridors/catalog.py` by the unordered
country pair, regardless of ticket ownership. Only these components participate:

1. Germany: the regional and long-distance OTP graphs and canonical registry.
2. DE–FR: a separate read-only SQLite timetable of actual cross-border trains.
3. France: the SNCF OTP graph and GTFS parent-station registry.

The legacy Europe OTP adapter remains in the repository for compatibility, but
the DE–FR search no longer invokes it or reads the Swiss archive. There is no
mandatory routing city. Candidate stations are discovered from train calls and
ranked geographically; geography never creates a train service.

The corridor timetable is intentionally not another OTP graph. Through ICE/TGV
and regional crossings are scheduled train edges retaining trip ID, service day,
direction and boarding/alighting restrictions. Country engines provide access
and onward journeys. Every crossing has a paid estimated fare. With an owned
Deutschlandticket, eligible German regional edges have zero marginal price;
without it, those schedules receive a provisional paid estimate. German
long-distance and French rail remain paid.

## Physical and temporal joins

TRO canonical station IDs remain database identities. Network IDs remain scoped
to their original feed. French hub metadata carries
`gtfs_stop_ids["fr-national-rail"]`, such as `StopArea:OCE87686006`. The French
provider discovers the running feed once per provider and sends
`feedId:StopArea:...`; new SNCF hubs never use name resolution.

The French city cache uses version 9; other country caches retain version 8.
Cached hub IDs are restored from persistent network memberships. No cache
deletion or database reset is needed.

Corridor parents attach to a unique canonical station using the exact source
ID, or matching normalized name plus a maximum 500m separation. A local endpoint
can attach using the same conservative rule. A nearby different terminal or
ambiguous match is not joined. Different stations need an explicit verified
transfer record; this first implementation does not invent walking links.

Country and corridor edges share that physical graph node. Train changes
require at least the configured minimum and the initial 15-minute same-station
floor. The route engine rejects missing/invalid train connection timestamps,
checks chronology even in whole-day mode, and counts waits and train changes
between separate rail edges. A through international train has no invented
border transfer. Onward country queries start after a scheduled crossing
arrival, including an arrival on the following day.

## Building and updating

Search requests accept `ranking_criterion`: `lowest_price` (the default),
`fastest`, or `balanced`. Ranking happens over all complete valid paths before
selecting ten results. Duration includes scheduled station waits. Balanced mode
normalizes each candidate's price and duration over their respective min/max
ranges and gives both dimensions equal weight. If one dimension is constant it
contributes zero. Scores are relative to the available candidate set, so new
timetable alternatives can change balanced ordering. The response echoes the
effective criterion in `route_plan.ranking_criterion` and `search_context`.

After refreshing the SNCF feed and station registries, run from the project root:

```powershell
docker compose exec backend python scripts/rail/build_de_fr_timetable.py
```

Or run the script locally with explicit `--source` and `--output` paths. Defaults
are `otp-data/france/france-sncf-gtfs.zip` and `data/rail/de-fr.sqlite`. Compose
sets `DE_FR_TIMETABLE_PATH=/app/data/rail/de-fr.sqlite`. The data volume preserves
the index across backend rebuilds. Builds write a temporary database and publish
it atomically; the source GTFS is unchanged. Searches open the index read-only
off the async event loop. A missing index produces a diagnostic and warning,
without falling back to Europe.

This SNCF-specific importer recognizes DE/FR UIC parent prefixes, accepts rail
route types 2 and 100–199, and excludes trips with unknown/third-country calling
points. It preserves calendar weekdays, additions/removals, GTFS hours beyond
24 and DST service-day semantics. It does not infer active dates from trip IDs.

The installed input produced 1,087 crossing trips and 109 stations, with active
service dates from 2026-10-20 through 2027-03-31. These counts describe this feed
snapshot, not guaranteed operator coverage or bookable ticket availability.

## First implementation limits and next increments

- Candidate search is bounded to eight matched stations per country; country
  providers retain their own query/itinerary limits. Results are not exhaustive.
- Missing cross-border operators need additional licensed/available timetable
  inputs and source adapters, rather than invented links. The initial index is
  sourced only from SNCF; it already contains regional and high-speed crossings.
- Translated station names with no exact source-ID or conservative normalized
  match are omitted. An audited station-alias/transfer import is the next step;
  nearest-neighbour matching alone must not replace it.
- The German package's regional→long-distance handover stage expects an origin
  city anchor. Reverse international egress currently benefits from its direct
  regional and direct long-distance stages; additional German multi-network
  onward handovers need the corresponding destination-side orchestration.
- Country queries have finite result windows; multiple arrival-conditioned
  onward query batches and candidate expansion are future completeness work.
- The crossing price uses a distance estimate with a wide uncertainty range,
  not a live fare. Separate ticket segments can overestimate a through fare.
- No transfer between different French terminals, frequency-based service
  expansion, real-time disruption handling or through-ticket guarantee is added.

For DE–AT, DE–CH and DE–NL, add a corridor manifest and a country-identification/
timetable importer, preserving network-scoped IDs and the same physical and
temporal join contract. Country graphs remain independent. A source adapter must
not assume SNCF prefixes apply to another operator's feed.
