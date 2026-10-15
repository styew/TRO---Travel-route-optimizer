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

### Database

The development version uses SQLite to cache approved city infrastructure and avoid unnecessary external API calls.

The database may be expanded in the future for:

* Caching API results
* Historical price data
* User accounts
* Travel history
* Machine learning datasets

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

The endpoint provides cached reference prices, not live bookable offers. TRO first checks the date-specific reference endpoint and, if it has no result, checks the calendar cache for the exact requested date before using the older cheapest-price fallback. TRO labels these prices accordingly and currently estimates flight duration from the airport distance. No booking link is created by this integration.

Airport discovery first reads IATA/ICAO codes from OpenStreetMap. If an Overpass outage requires the Nominatim airport fallback, TRO also attempts a public Wikidata lookup using the airport's Wikidata identifier. An airport node without an IATA code remains visible in the graph, but cannot be used for a Travelpayouts airport-pair search; the API reports that limitation explicitly.

Airport selection is city-agnostic: TRO ranks commercial candidates inside the configured 50 km radius by city-name relevance, passenger-service signals, IATA availability, and distance. It returns at most two principal candidates. If an airport's name does not contain the city name, a nearby commercial candidate can still be selected instead of being lost to a fragile name-only rule.

TRO also makes one bounded comparison pass for departure-airport gateways. It checks up to three nearby IATA-tagged commercial airports within a 220 km discovery radius and keeps only gateways reachable in at most three hours by the current car estimate. This makes routes such as `car -> Memmingen Airport -> flight` possible and lets their total cost compete with a direct departure from Nürnberg, without querying every airport in Germany. It does not assume a rental car after landing.

When running the backend with Docker, pass the environment file explicitly because Docker does not copy `.env` into the image:

```powershell
docker run --name tro-backend --env-file .env -p 8000:8000 backend:latest
```

---

## Regional Rail and Deutschlandticket

TRO uses the free **GTFS Regional Rail Germany** dataset with a local OpenTripPlanner (OTP) service to calculate German regional-rail itineraries. The data covers regional services such as RB, RE, IRE and S-Bahn. It is intentionally separate from commercial train search and does not provide ticket prices or booking links.

When the traveller confirms that they already have a Deutschlandticket, TRO adds regional itineraries as `train_connection` graph edges with a zero marginal ticket cost. The planned times, operators, internal transfers, and the scheduled waiting time at every train-to-train transfer are retained in the edge metadata. The planner accepts the complete locally filtered regional feed, including regional operator labels that are not simply RB or RE, while still rejecting clear long-distance services. This zero cost does not mean a Deutschlandticket can be purchased for free.

The search also accepts a local `departure_time` (`HH:MM`, Europe/Berlin). Direct car estimates and car access to a nearby gateway station receive planned departure and arrival times. A gateway train search begins only after the estimated drive plus a ten-minute parking/platform buffer, so an earlier train cannot be proposed after driving to that station.

## Commercial Rail and Omio

Paid rail search is represented in the backend by a provider boundary named `CommercialTrainProvider`. Its initial implementation, `OmioTrainProvider`, intentionally makes no request until TRO receives approved Omio partner credentials and the partner-specific API contract. The public browser never receives an API key or affiliate marker.

After approval, the provider will return timed, priced train edges with booking URLs. The next composition stage will join a zero-marginal-cost Deutschlandticket edge to a paid Omio edge at an eligible interchange station, respecting the incoming train arrival and a minimum transfer time. Consecutive regional and paid train legs remain the same `train` transport category, although they are distinct booked services.

### Starting the local regional-train planner

Download the current GTFS feed, build the OTP graph, then start both services:

```powershell
.\scripts\update_regional_gtfs.ps1
..\.venv\Scripts\python.exe .\scripts\build_d_ticket_gtfs.py
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

This project will be developed iteratively as a solo project.

The initial documentation describes the current direction of the project and is intentionally kept flexible.

Requirements, technologies and the project scope may change during development based on:

* Technical feasibility
* Data availability
* API limitations
* Testing
* User feedback

The documentation will be updated as the project evolves.
