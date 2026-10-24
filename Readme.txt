# Travel Route Optimizer

## Overview

Travel Route Optimizer is an application designed to calculate and compare the most efficient ways to travel from point A to point B.

The application will consider different transportation methods, including:

* Car
* Train
* Airplane

It will also consider combinations of these transportation methods.

The user will be able to optimize the search according to:

* Lowest price
* Shortest travel time
* Best balance between price and travel time

Whenever possible, the application will provide booking links for the train or flight segments in a selected route. The user completes the purchase directly with the respective transportation provider.

---

## Problem and Motivation

There are many applications that calculate travel costs and times between two locations. However, most travel search systems focus primarily on individual transportation methods or predefined connections.

The cheapest flight is not necessarily the cheapest way to complete the entire journey.

For example, a traveler from Erfurt may find a very cheap flight from Nürnberg to Mallorca. Even after considering the cost of traveling from Erfurt to Nürnberg, this combination could still be cheaper than taking a flight directly from a closer airport.

The goal of this project is to explore this gap by combining different transportation methods and evaluating the complete journey rather than individual travel segments.

---

## Goal and Scope

The main goal is to find and compare efficient routes between two locations using multiple transportation methods.

### Initial Transportation Modes

* Car
* Train
* Airplane

### Future Transportation Modes

* Bus
* Boat

### Initial Optimization Criteria

1. Price
2. Travel time
3. Price/time balance

### Initial Target Region

Germany will be the primary starting region, with destinations initially focused on Europe.

### Booking Links

Travel Route Optimizer is a route comparison and discovery tool. It does not sell tickets, process payments, issue tickets, or manage booking changes and cancellations.

For a selected route, the application may show a booking link for each train or flight segment that can be purchased from an available provider. Booking links may be supplied by approved partners and will be identified transparently when applicable.

### Out of Scope

The following features are not part of the initial version:

* Hotels
* Car rental
* Restaurants
* Tourism recommendations
* Ticket payments
* Ticket issuance
* Booking changes, cancellations, or customer support for transportation providers

Additional transportation methods and features may be introduced in future versions.

---

## Core Concept

### Example

**Origin:** Erfurt, Germany
**Destination:** Mallorca, Spain

The system could generate several possible routes.

### Route A

```text
Erfurt
   ↓ 
Mallorca

Price: €145
Travel time: 2h 55min
```

### Route B

```text
Erfurt
   ↓ 
Nürnberg
   ↓ 
Mallorca

Price: €80
Travel time: 6h
```

### Route C

```text
Erfurt
   ↓ 
Frankfurt
   ↓ 
Mallorca

Price: €110
Travel time: 4h 30min
```

The system could then identify:

```text
Cheapest:
Route B — €80

Fastest:
Route A — 2h 55min

Best Price/Time Balance:
Route C
```

When available, the system should also provide links to the corresponding flight or train provider.

---

## Initial Requirements

### FR-01

The user can specify an origin and destination.

### FR-02

The user can specify a travel date.

### FR-03

The system can consider multiple transportation modes.

### FR-04

The system can calculate the estimated total price of a route.

### FR-05

The system can calculate the estimated total travel time.

### FR-06

The system can compare multiple possible routes.

### FR-07

The system can rank routes according to price or travel time.

### FR-08

The system can combine different transportation modes within a single route.

### FR-09

The system should provide a booking link to the corresponding transportation provider for each bookable train or flight segment when available.

### FR-10

The system should clearly identify booking links that are provided through a commercial or partner relationship.

---

## Technical Direction

### Frontend

* HTML
* CSS
* JavaScript

A frontend framework such as React may be considered in the future if the complexity of the application justifies it.

### Backend

* Python
* FastAPI

### Data Processing

* Python
* Pandas

### External Data

The application will rely on external data sources and APIs for information such as:

* Geocoding and geographic coordinates
* Road distance and travel time
* Fuel prices
* Flight connections and prices
* Train connections and prices
* Booking links for available train and flight providers

The exact providers will be evaluated during the initial research phase.

### Flight Data Levels

Travelpayouts provides cached price references. These are useful to compare a
route, but may contain only the total journey duration and number of stops. A
cached reference must never be presented as a live or bookable offer.

For a complete flight itinerary, TRO supports an optional Duffel integration.
When `DUFFEL_ACCESS_TOKEN` is configured on the backend, live EUR offers are
queried first and the interface can show every flight segment, airline,
departure and arrival airport, scheduled times, and each layover duration.
Live offers expire quickly and are therefore not stored in the 24-hour flight
reference cache. Without this optional credential, TRO transparently falls
back to Travelpayouts and labels any unavailable layover detail.

### Database

The development version uses SQLite to cache approved city infrastructure and avoid unnecessary external API calls.

The database may be expanded in the future for:

* Caching API results
* Historical price data
* User accounts
* Travel history
* Machine learning datasets

### Rail corridor strategy

TRO does not keep a worldwide rail graph active for every search. A small
metadata catalog selects only the relevant timetable corridor from the origin
and destination countries. The first planned international handover is
Germany to France: regional German travel can be covered by an existing
Deutschlandticket up to a configured gateway station, while the onward
international rail segment receives a separately labelled price estimate.
This keeps the runtime practical while allowing additional country corridors,
buses and ferries to be added later.

### Local database inspection and analysis

The development database is the local SQLite file `data/tro.db`, mounted into
the backend container. It is useful to inspect the cache and flight data
directly, but the public API does not expose a database administration route:
that would be unsafe without user authentication.

Run the read-only terminal inspector while Docker is running:

```powershell
.\.venv\Scripts\python.exe .\scripts\database\inspect_database.py
.\.venv\Scripts\python.exe .\scripts\database\inspect_database.py --table scheduled_flights --limit 20
.\.venv\Scripts\python.exe .\scripts\database\inspect_database.py --table flight_price_observations
```

For reproducible pandas reports, run:

```powershell
.\.venv\Scripts\python.exe .\scripts\flights\analyze_flights.py
```

This writes `scheduled_flights_summary.csv` and
`flight_price_observations_summary.csv` under `data/analysis/`. These are
derived reports: they never modify route caches or API data.

For a visual table editor, install **DB Browser for SQLite** and open
`data/tro.db` in read-only mode while TRO is running. Before manually editing
data, stop the backend container and make a copy of the database file. Direct
editing during active searches can create locks or invalidate cached results.

### Manual rail-price observations

`rail_price_observations` is a separate preparation table for real rail prices
collected manually. It is not used by route calculation yet, so adding rows
cannot change existing route results. Each row describes the price of a whole
ticket itinerary, rather than an individual train leg. This makes it suitable
for later estimate calibration, because one ticket can cover several trains.

Create the table and one clearly labelled non-real example row with:

```powershell
.\.venv\Scripts\python.exe .\scripts\rail\prepare_rail_price_observations.py
```

Use `scripts/rail/rail_price_observations_template.csv` as a safe spreadsheet
template. In DB Browser for SQLite, open `data/tro.db`, choose the
`rail_price_observations` table and remove the row where `is_example` is true
before analysing real observations. Keep a consistent search profile (for
example: one adult, second class, no discount card and no seat reservation)
so that the data can be compared later.

`price_eur` stores one observed price or the midpoint of a reported range.
When a source only provides a range, `price_min_eur` and `price_max_eur`
preserve its lower and upper bounds instead of pretending that the midpoint is
an exact fare. The included `scripts/rail/import_user_rail_price_observations.py` script
records the initial ICE calibration supplied during project development. It is
idempotent and does not affect route results.

### Prototype rail-price profiles

TRO also has a separate `rail_price_profiles` table for compact, explainable
price rules. A profile covers a market segment such as German long-distance
rail between 400 and 750 km booked zero to two days ahead. It is not a list of
city pairs and it never claims a current fare.

The initial pilot contains low-confidence ICE profiles split by rough
rail-distance band (100–199 km, 200–399 km, 400–599 km and 600–750 km) and
booking horizon (zero to two days or three to 21 days). They are calibrated
from project-owner observations for Berlin–Hamburg, Berlin–Kiel,
Berlin–Leipzig, Berlin–Düsseldorf, Frankfurt–Köln, München–Augsburg,
Stuttgart–Hamburg and München–Berlin. A very narrow München–Salzburg
prototype also distinguishes RJ/EC from RE, but it must not be generalized to
other international services. These remain low confidence until authorised
observations can calibrate more markets, travel times, discount products and
train types.

European calibration observations can additionally use `market` pairs such
as `DE-FR` and an optional seller/operator context such as `db` or `oebb`.
This avoids incorrectly averaging two materially different offers for the
same route. `scripts/rail/import_user_european_rail_observations.py` contains the
initial owner-provided Omio research for München to Paris, Wien, Zürich,
Amsterdam, Milano, Roma and Zagreb. These route-level records retain stated
transfer stations and always represent an observed full itinerary, not the
sum of individual train legs.

### Statistical rail-price estimates

TRO can derive price profiles from classified observations rather than relying
only on manually written ranges. Each usable observation needs a market,
service category, provider context and estimated itinerary distance. Until a
European timetable is imported, the initial route distances are documented
approximations. The timetable will later replace them with actual itinerary
distances.

Run these maintenance scripts after importing new curated observations:

```powershell
.\.venv\Scripts\python.exe .\scripts\rail\enrich_rail_price_observation_metadata.py
.\.venv\Scripts\python.exe .\scripts\rail\rebuild_rail_price_profiles.py
```

The derived model uses the median of comparable observations, then includes a
EUR 20 time-of-day allowance reported during project research. Its confidence
is based on sample size: very low for one or two observations, low for three
to seven, medium for eight to 19, and high for 20 or more. A profile remains
an estimate and never confirms ticket availability.

### German long-distance rail graph

The Deutschlandticket graph and paid German long-distance rail are deliberately
separate. The first contains filtered regional services only; the second uses
the official GTFS.DE long-distance feed, which includes ICE, IC, EC, railJet
and night trains. Keeping the graphs separate prevents a paid ICE service from
being accidentally treated as a Deutschlandticket route.

Download the current long-distance feed and build its local OpenTripPlanner
graph before enabling schedule-based paid-train discovery:

```powershell
.\scripts\rail\update_long_distance_gtfs.ps1
docker compose --profile long-distance-build run --rm otp-long-distance-build
docker compose up -d --build
```

The downloaded GTFS archive is an input artifact, while `graph.obj` is the
query-optimized routing graph. Keep the archive until the graph has been
validated and record its source/version for reproducibility. Raw archives can
then be archived outside the running server; the SQLite database stores only
TRO metadata and fare observations, not every timetable row.

GTFS.DE publishes a rolling timetable window rather than an entire year. The
current graph only returns journeys whose selected date is inside that feed's
calendar range. Refresh and rebuild this graph whenever a later travel date is
needed; the feed metadata should be stored alongside each refresh.

### European rail expansion (France, Switzerland, Italy and Austria)

TRO now has a staging area for an optional shared European rail graph under
`otp-data/europe/`. It keeps each national GTFS archive as a distinct source
feed, then creates rail-only copies for OpenTripPlanner. This is deliberately
not enabled in the normal backend yet: a Frankfurt-to-Paris or München-to-Wien
journey must first receive explicit, tested station-transfer links at borders.
Simply placing four independent timetables in one routing engine does not
guarantee that it can change trains between them.

The selected primary sources are:

* France: SNCF national GTFS (TGV, Intercités and TER), downloadable directly
  through the French national transport-data portal.
* Switzerland: the national Swiss public-transport GTFS. It includes more than
  rail, so TRO filters it to rail before building.
* Austria: ÖBB GTFS, provided under CC BY 4.0 after the downloader accepts the
  portal's terms.
* Italy: the Italian National Access Point publishes the official Trenitalia
  schedule as NeTEx. It must be converted and validated into GTFS before it is
  staged; TRO does not silently rely on an unofficial conversion.

Download France or Switzerland directly:

```powershell
.\scripts\rail\update_european_gtfs.ps1 -Country France
.\scripts\rail\update_european_gtfs.ps1 -Country Switzerland
```

After manually downloading a valid **GTFS ZIP** from an official source, stage
the other countries explicitly. This lets the project keep the source and its
licence decision auditable:

```powershell
.\scripts\rail\update_european_gtfs.ps1 -Country Austria -SourcePath "C:\Downloads\oebb-gtfs.zip"
```

Then make the rail-only OTP inputs and build the optional Europe graph:

```powershell
.\.venv\Scripts\python.exe .\scripts\rail\build_european_rail_feeds.py
docker compose --profile europe-build run --rm otp-europe-build
docker compose --profile europe up -d otp-europe
```

For a large national source such as Switzerland, process it independently and
wait for the completion message before rebuilding the OTP graph:

```powershell
.\.venv\Scripts\python.exe .\scripts\rail\build_european_rail_feeds.py --country switzerland
```

Do not stage Italy until its official NeTEx archive has passed a dedicated,
documented NeTEx-to-GTFS conversion and validation step. The raw archives and
the generated graph are local runtime data, ignored by Git.

On application startup, TRO creates `rail_price_profiles` and seeds these
profiles once. Existing profiles are never overwritten automatically, so they
can later be adjusted through a controlled import or database administration
workflow.

Use the testing endpoint to inspect the model without changing a route graph:

```powershell
curl -X POST http://127.0.0.1:8000/rail-price-estimate `
  -H "Content-Type: application/json" `
  -d '{"market":"DE","service_category":"long_distance","distance_km":505,"booking_days_ahead":7}'
```

The response contains `estimated_min_eur`, `estimated_typical_eur`,
`estimated_max_eur`, the matching `profile_key`, the source, and a confidence
label. No live supplier request occurs. Once the separate official GTFS.DE
long-distance graph has been built, the production graph uses these profiles
for domestic German long-distance rail routes. The timetable comes from
GTFS.DE, while the fare remains an estimate and never a live supplier offer.

### Optimization

Graph-based algorithms will be used to model and evaluate possible routes.

### Machine Learning

Machine Learning is **not part of the initial version**.

It may be considered in future versions for features such as:

* Price prediction
* Delay prediction
* Route recommendations
* Personalized travel suggestions

### Infrastructure

* Docker
* Docker Compose

---

## Flight Reference Prices

TRO can optionally use the Travelpayouts Aviasales Data API to add flight edges between discovered airport nodes that have IATA codes.

1. Copy `.env.example` to `.env`.
2. Set `TRAVELPAYOUTS_API_TOKEN` to the private token from the Travelpayouts profile.
3. Keep `.env` private. It is ignored by Git and must never be committed.

The endpoint provides cached reference prices, not live bookable offers. TRO first checks the date-specific reference endpoint and, if it has no result, checks the calendar cache for the exact requested date before using the older cheapest-price fallback. TRO labels these prices accordingly and uses the itinerary duration when the date-specific response supplies it; older cached responses retain a clearly labelled distance-based duration estimate. No booking link is created by this integration.

The cached reference can report a departure timestamp and a number of stops, but it does not provide the airport and waiting time for every layover. TRO therefore never invents those details. A future detailed-itinerary flight provider will model each flight leg and airport transfer separately in the graph.

Travelpayouts documents `duration` in minutes, including waiting time. TRO still validates obvious contradictions in cached data: a zero-stop reference whose reported duration is far above a geographic direct-flight estimate is relabelled as an estimate instead of being treated as a trustworthy itinerary duration. The raw provider duration remains visible in the response for diagnostics.

### Flight-reference cache

TRO keeps a separate 24-hour database cache for a successful flight reference, keyed by origin IATA code, destination IATA code, and travel date. A cache is a temporary stored copy: it reduces repeated provider calls and lets the graph remain available if Travelpayouts later returns an empty response or has a short outage. A current provider response always replaces the stored copy. When TRO uses the stored copy, the response and frontend label it as a **stored reference**, show when it was saved, and still require confirmation before booking. The cache never claims that an old price is a live fare.

Airport discovery first reads IATA/ICAO codes from OpenStreetMap. If an Overpass outage requires the Nominatim airport fallback, TRO also attempts a public Wikidata lookup using the airport's Wikidata identifier. An airport node without an IATA code remains visible in the graph, but cannot be used for a Travelpayouts airport-pair search; the API reports that limitation explicitly.

Airport selection is city-agnostic: TRO ranks commercial candidates inside the configured 50 km radius by city-name relevance, passenger-service signals, IATA availability, and distance. It returns at most two principal candidates. If an airport's name does not contain the city name, a nearby commercial candidate can still be selected instead of being lost to a fragile name-only rule.

TRO also makes one bounded comparison pass for departure-airport gateways. It checks up to three nearby IATA-tagged commercial airports within a 220 km discovery radius and keeps only gateways reachable in at most three hours by the current car estimate. This makes routes such as `car -> Memmingen Airport -> flight` possible and lets their total cost compete with a direct departure from Nürnberg, without querying every airport in Germany. It does not assume a rental car after landing.

Selected gateway airports are also cached for 30 days per origin city. This is separate from the 24-hour flight-reference cache: airport candidates such as Frankfurt Airport remain available as graph nodes and car-access edges if the public geographic lookup later has a temporary empty response. A complete `car -> airport -> flight` route still requires a priced flight edge to the destination.

### Published flight schedules and price estimates

TRO can also store **published flight schedules** separately from flight prices.
An airport schedule (for example, a permitted export from Nürnberg Airport) tells
the graph that a flight is planned, with its airline, flight number, origin,
destination, and times. It does **not** prove that a seat is available or give a
current price. TRO therefore never labels an imported schedule as a bookable
offer.

Use only an official export or data that the source permits TRO to reuse. Do not
scrape airport, airline, or affiliate webpages. The local importer expects one
actual service date per CSV row; a documented template is available at
`scripts/flights/flight_schedule_template.csv`.

```powershell
# Local Python installation
.\.venv\Scripts\python.exe .\scripts\flights\import_flight_schedules.py .\my-nuremberg-export.csv

# Or, after rebuilding the Docker image, using a CSV stored under ./data
docker compose exec backend python scripts/flights/import_flight_schedules.py /app/data/my-nuremberg-export.csv
```

The importer creates `scheduled_flights` records and updates an existing row
when the same sourced flight movement is imported again. This is intentionally
not yet a live-price system. Each successful Travelpayouts flight reference is
also recorded in `flight_price_observations` with its observed date, flight
date, route, duration, and stop count. Those observations will allow the next
graph stage to display a clearly marked historical price range (low / typical /
high and confidence) for a scheduled flight instead of inventing a live fare.
No price is required when adding a schedule.

### AeroDataBox scheduled-flight refresh

When `AERODATABOX_RAPIDAPI_KEY` is set in the private `.env` file, TRO uses
the AeroDataBox FIDS endpoint to refresh published departures for the selected
origin airport and date. It sends two 12-hour requests only when that
airport/day is absent or older than seven days in the local cache. A successful
empty result is cached too, so repeated searches do not consume API quota when
there is no direct departure to the selected destination airport.

The service stores only matching direct passenger departures and creates a
flight graph edge with its airline, flight number, airport pair, scheduled
departure and scheduled arrival. The free AeroDataBox plan requires
attribution, which the frontend displays whenever schedule data is used.

Schedule data is not a ticket offer. TRO first uses a historical
Travelpayouts-derived price range when one exists for the airport pair; if it
does not, it uses a clearly marked low-confidence route-length estimate. The
user must still confirm availability and price elsewhere.

The first version intentionally models direct scheduled flights. Building a
multi-flight itinerary with an intermediate airport is the next graph stage:
it requires loading eligible onward departures at that airport and checking a
safe transfer time before creating separate connected flight edges.

When running the backend with Docker, pass the environment file explicitly because Docker does not copy `.env` into the image:

```powershell
docker run --name tro-backend --env-file .env -p 8000:8000 backend:latest
```

---

## Regional Rail and Deutschlandticket

TRO uses the free **GTFS Regional Rail Germany** dataset with a local OpenTripPlanner (OTP) service to calculate German regional-rail itineraries. The data covers regional services such as RB, RE, IRE and S-Bahn. It is intentionally separate from commercial train search and does not provide ticket prices or booking links.

When the traveller confirms that they already have a Deutschlandticket, TRO adds regional itineraries as `train_connection` graph edges with a zero marginal ticket cost. The planned times, operators, internal transfers, and the scheduled waiting time at every train-to-train transfer are retained in the edge metadata. The planner accepts the complete locally filtered regional feed, including regional operator labels that are not simply RB or RE, while still rejecting clear long-distance services. This zero cost does not mean a Deutschlandticket can be purchased for free.

The search accepts a local `departure_time` (`HH:MM`, Europe/Berlin) and enables schedule matching by default. Direct car estimates and car access to nearby gateways receive planned departure and arrival times. A gateway train search begins only after the estimated drive plus a ten-minute parking/platform buffer. A car-to-flight path also requires a 90-minute airport-arrival buffer, and a cached flight without a departure timestamp cannot be joined to a preceding car leg. Set `respect_departure_time` to `false` to compare available options across the selected day instead; the selected start time then does not eliminate a route, and regional rail is queried across a 24-hour window.

## Commercial Rail and Omio

Paid rail search is represented in the backend by a provider boundary named `CommercialTrainProvider`. Its initial implementation, `OmioTrainProvider`, intentionally makes no request until TRO receives approved Omio partner credentials and the partner-specific API contract. The public browser never receives an API key or affiliate marker.

After approval, the provider will return timed, priced train edges with booking URLs. The next composition stage will join a zero-marginal-cost Deutschlandticket edge to a paid Omio edge at an eligible interchange station, respecting the incoming train arrival and a minimum transfer time. Consecutive regional and paid train legs remain the same `train` transport category, although they are distinct booked services.

### Starting the local regional-train planner

Download the current GTFS feed, build the OTP graph, then start both services:

```powershell
.\scripts\rail\update_regional_gtfs.ps1
.\.venv\Scripts\python.exe .\scripts\rail\build_d_ticket_gtfs.py
docker compose --profile build run --rm otp-build
docker compose up --build
```

The first OTP graph build takes longer than normal backend startup. It creates local files under `otp-data/`, which are ignored by Git. OTP exposes a local GraphQL endpoint on port `8080`; the TRO backend continues to work with car and flight edges if OTP is temporarily unavailable.

---

## Assumptions and Limitations

The application will initially provide estimated travel prices and times.

Actual prices and travel times may differ due to factors such as:

* Changing ticket prices
* Fuel price changes
* Availability
* Traffic
* Delays
* Changes in transportation schedules

The price, availability, booking conditions, and final itinerary are confirmed by the transportation provider at the time of purchase. Travel Route Optimizer only presents route comparisons and redirects the user to the relevant provider when a booking link is available.

The availability and quality of external APIs will also influence which transportation options can be provided.

---

## Project Development

For the current technical layout, data boundaries and maintenance-script
groups, see [docs/architecture.md](docs/architecture.md).

This project will be developed iteratively as a solo project.

The initial documentation describes the current direction of the project and is intentionally kept flexible.

Requirements, technologies and the project scope may change during development based on:

* Technical feasibility
* Data availability
* API limitations
* Testing
* User feedback

The documentation will be updated as the project evolves.
