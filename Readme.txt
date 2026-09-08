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

Whenever possible, the application will also provide a link to the corresponding flight or train connection so that the user can continue the booking process with the respective provider.

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

### Out of Scope

The following features are not part of the initial version:

* Hotels
* Car rental
* Restaurants
* Tourism recommendations

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
   ↓ ✈️
Mallorca

Price: €145
Travel time: 2h 55min
```

### Route B

```text
Erfurt
   ↓ 🚗
Nürnberg
   ↓ ✈️
Mallorca

Price: €80
Travel time: 6h
```

### Route C

```text
Erfurt
   ↓ 🚆
Frankfurt
   ↓ ✈️
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

The system should provide a link to the corresponding transportation provider when available.

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

The exact providers will be evaluated during the initial research phase.

### Database

**Not required for V1.**

A database may be introduced in the future if persistent data becomes useful for:

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

## Assumptions and Limitations

The application will initially provide estimated travel prices and times.

Actual prices and travel times may differ due to factors such as:

* Changing ticket prices
* Fuel price changes
* Availability
* Traffic
* Delays
* Changes in transportation schedules

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
