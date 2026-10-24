"""Compose a country-pair timetable with independently routed country legs."""

from __future__ import annotations

import asyncio
import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from backend.database.models import RailStationMembershipRecord, RailStationRecord
from backend.database.session import SessionLocal
from backend.models.graph import TravelEdge
from backend.models.node import TransportNode
from backend.rail.corridors.catalog import CorridorManifest
from backend.rail.corridors.timetable import open_timetable, service_instant
from backend.rail.france_connection_provider import discover_french_rail_edges
from backend.rail.germany_connection_provider import discover_german_network_edges
from backend.rail.station_registry import _distance_km, normalize_station_name
from backend.services.international_rail_provider import _provisional_price


def _same_station(node: TransportNode, name: str, lat: float, lon: float) -> bool:
    """Require a matching normalized name AND at most 500m; proximity alone fails."""
    return (
        normalize_station_name(node.name) == normalize_station_name(name)
        and _distance_km(node.latitude, node.longitude, lat, lon) <= 0.5
    )


def _station_nodes(db, endpoints: list[TransportNode], country: str, limit: int) -> dict[str, TransportNode]:
    """Link corridor parents to unique physical registry stations or local terminals.

    An ambiguous name/location match is excluded from domestic composition.
    Unmatched corridor stations remain usable for direct journeys only when a
    local endpoint supplies the same conservative physical-station match.
    """
    with SessionLocal() as session:
        records = list(session.execute(
            select(RailStationRecord, RailStationMembershipRecord)
            .join(RailStationMembershipRecord, RailStationMembershipRecord.station_id == RailStationRecord.id)
            .where(RailStationMembershipRecord.network_key.in_(
                {"de-regional", "de-long-distance"} if country == "DE" else {"fr-national-rail"}
            ))
        ).all())
    by_name, by_stop = defaultdict(list), defaultdict(list)
    for station, membership in records:
        by_name[station.normalized_name].append((station, membership))
        by_stop[membership.source_stop_id].append((station, membership))
    center = next((node for node in endpoints if node.type == "city_center"), endpoints[0])
    result = {}
    for row in db.execute("SELECT * FROM stations WHERE country = ?", (country,)):
        exact = [node for node in endpoints if node.type == "train_station" and (
            row["id"] in node.metadata.get("gtfs_stop_ids", {}).values()
            or _same_station(node, row["name"], row["lat"], row["lon"]))]
        matches = {}
        candidates = by_stop[row["id"]] or by_name[normalize_station_name(row["name"])]
        for station, membership in candidates:
            if membership.source_stop_id == row["id"] or (
                _distance_km(station.latitude, station.longitude, row["lat"], row["lon"]) <= 0.5
            ):
                matches.setdefault(station.canonical_id, (station, {}))[1][membership.network_key] = membership.source_stop_id
        if len(exact) == 1:
            node = exact[0].model_copy(deep=True)
            if len(matches) == 1:
                node.metadata.setdefault("gtfs_stop_ids", {}).update(next(iter(matches.values()))[1])
        elif len(matches) == 1:
            station, ids = next(iter(matches.values()))
            node = TransportNode(
                id=f"corridor-station:{station.canonical_id}", type="train_station",
                name=station.name, latitude=station.latitude, longitude=station.longitude,
                supported_modes=["train"], source="canonical corridor station registry",
                source_id=station.canonical_id,
                metadata={"gtfs_stop_ids": ids, "country_code": country},
            )
        else:
            continue
        node.metadata["corridor_parent_id"] = row["id"]
        if country == "FR":
            node.metadata.setdefault("gtfs_stop_ids", {})["fr-national-rail"] = row["id"]
        result[row["id"]] = node
    ranked = sorted(result, key=lambda key: (
        result[key].id not in {node.id for node in endpoints},
        _distance_km(center.latitude, center.longitude, result[key].latitude, result[key].longitude), key,
    ))
    return {key: result[key] for key in ranked[:limit]}


def corridor_edges(db, manifest: CorridorManifest, origins: dict[str, TransportNode],
                   destinations: dict[str, TransportNode], travel_date: str,
                   departure_time: str, passengers: int, minimum_transfer: int) -> list[TravelEdge]:
    """Read actual train calls, calendars and direction; every crossing is paid.

    A through ICE/TGV is one edge with zero transfers. A regional crossing is
    also one paid edge. Country planners provide separate access/egress edges.
    No connection is invented from geographical proximity or station order.
    """
    day = date.fromisoformat(travel_date)
    requested = datetime.combine(day, time.fromisoformat(departure_time), ZoneInfo(manifest.timezone))
    end = datetime.combine(day + timedelta(days=1), time.min, ZoneInfo(manifest.timezone))
    edges = []
    # Include prior service days for trains using GTFS 24:xx/25:xx times.
    max_seconds = db.execute("SELECT COALESCE(MAX(departure), 0) FROM calls").fetchone()[0]
    first = day - timedelta(days=max_seconds // 86400)
    for parent_from, origin in origins.items():
        for parent_to, destination in destinations.items():
            rows = db.execute("""
                SELECT t.id, t.line, t.name, d.day, a.departure, b.arrival
                FROM calls a JOIN calls b ON a.trip = b.trip AND a.sequence < b.sequence
                JOIN trips t ON t.id = a.trip JOIN dates d ON d.service = t.service
                WHERE a.station = ? AND b.station = ? AND a.pickup = 0 AND b.dropoff = 0
                AND d.day BETWEEN ? AND ? ORDER BY d.day, a.departure
            """, (parent_from, parent_to, first.isoformat(), travel_date))
            for row in rows:
                service_day = date.fromisoformat(row["day"])
                departure = service_instant(service_day, row["departure"], manifest.timezone)
                arrival = service_instant(service_day, row["arrival"], manifest.timezone)
                if not requested <= departure < end or arrival <= departure:
                    continue
                distance = _distance_km(origin.latitude, origin.longitude, destination.latitude, destination.longitude) * 1.15
                price = _provisional_price(distance, passengers)
                edges.append(TravelEdge(
                    id=f"train:corridor:{manifest.key}:{row['id']}:{row['day']}:{parent_from}:{parent_to}",
                    origin_node_id=origin.id, destination_node_id=destination.id,
                    mode="train", edge_type="train_connection", estimated_distance_km=round(distance, 2),
                    estimated_duration_minutes=round((arrival.timestamp() - departure.timestamp()) / 60),
                    estimated_price_eur=price["typical_eur"], is_estimate=True,
                    provider=f"GTFS corridor {manifest.key}", metadata={
                        **price, "corridor": manifest.key, "gtfs_trip_id": row["id"],
                        "ticket_coverage": "estimated paid cross-border rail fare",
                        "deutschlandticket_eligible": False, "transfer_count": 0,
                        "required_preceding_transfer_minutes": minimum_transfer,
                        "planned_departure": departure.isoformat(), "planned_arrival": arrival.isoformat(),
                        "legs": [{"from": origin.name, "to": destination.name, "line": row["line"],
                                  "route": row["name"], "scheduled_departure": departure.isoformat(),
                                  "scheduled_arrival": arrival.isoformat()}],
                    },
                ))
    return edges


def _load_crossings(manifest, origins, destinations, origin_country, destination_country, travel_date, departure_time, passengers, minimum):
    """Read SQLite and physical registry bindings off the API event loop."""
    db = open_timetable(manifest.index_path)
    try:
        origin_map = _station_nodes(db, origins, origin_country, manifest.max_stations_per_country)
        destination_map = _station_nodes(db, destinations, destination_country, manifest.max_stations_per_country)
        edges = corridor_edges(db, manifest, origin_map, destination_map, travel_date, departure_time, passengers, minimum)
        return origin_map, destination_map, edges
    finally:
        db.close()


async def discover_corridor_edges(manifest: CorridorManifest | None, origin_nodes: list[TransportNode],
                                  destination_nodes: list[TransportNode], origin_country: str,
                                  destination_country: str, travel_date: str, departure_time: str,
                                  has_deutschlandticket: bool, passengers: int, minimum_transfer: int,
                                  search_entire_day: bool) -> tuple[list, list, list, dict]:
    """Compose two country packages and a bidirectional crossing timetable.

    Country-to-corridor joins share one verified physical node. Every onward
    edge carries the configured transfer buffer; final chronology is checked
    by the route engine even when comparing the entire travel day.
    """
    if manifest is None:
        return [], [], [], {"enabled": False}
    minimum = max(15, minimum_transfer)
    try:
        origin_map, destination_map, crossings = await asyncio.to_thread(
            _load_crossings, manifest, origin_nodes, destination_nodes, origin_country,
            destination_country, travel_date, "00:00" if search_entire_day else departure_time, passengers, minimum,
        )
    except (OSError, sqlite3.Error) as error:
        return [], [], [{"infrastructure_type": "international_rail", "message": "Build the DE–FR corridor timetable before searching international rail."}], {"enabled": True, "available": False, "reason": type(error).__name__}
    # Only ask country engines about stations with an actual crossing today.
    from_ids = {edge.origin_node_id for edge in crossings}
    to_ids = {edge.destination_node_id for edge in crossings}
    gateways_from = [node for node in origin_map.values() if node.id in from_ids]
    gateways_to = [node for node in destination_map.values() if node.id in to_ids]
    nodes, edges, warnings = [], list(crossings), []
    reports = {}
    for country, starts, ends, stage in (
        (origin_country, origin_nodes, gateways_from, "access"),
        (destination_country, gateways_to, destination_nodes, "egress"),
    ):
        # Same-station direct international departures need no domestic edge.
        start_ids = {node.id for node in starts}
        end_ids = {node.id for node in ends}
        starts = [node for node in starts if node.id not in end_ids]
        ends = [node for node in ends if node.id not in start_ids]
        if not starts or not ends:
            continue
        stage_date, stage_time, stage_entire_day = travel_date, departure_time, search_entire_day
        if stage == "egress":
            # A night crossing can arrive on the next calendar day. Start the
            # country query after a real crossing arrival rather than midnight
            # on the original date, and retain chronology in the route engine.
            first_arrival = min(datetime.fromisoformat(edge.metadata["planned_arrival"]) for edge in crossings)
            onward = first_arrival + timedelta(minutes=minimum)
            stage_date, stage_time, stage_entire_day = onward.date().isoformat(), onward.strftime("%H:%M"), False
        if country == "DE":
            operation = discover_german_network_edges(
                starts, ends, stage_date, stage_time, True,
                passengers, minimum, stage_entire_day,
            )
        else:
            operation = discover_french_rail_edges(
                starts, ends, stage_date, stage_time, passengers, minimum, stage_entire_day,
            )
        try:
            result = await asyncio.wait_for(operation, timeout=30)
        except TimeoutError:
            warnings.append({"infrastructure_type": "international_rail", "message": f"The {country} {stage} timetable search exceeded its time budget; direct crossings were retained."})
            reports[stage] = {"timed_out": True}
            continue
        if country == "DE":
            extra, domestic, notices, report = result
            nodes.extend(extra)
            # The existing regional adapter returns ticket-covered schedules.
            # Without an owned ticket the identical eligible domestic trains
            # remain available, but their entire regional leg is priced.
            if not has_deutschlandticket:
                for edge in domestic:
                    if edge.metadata.get("ticket_coverage") == "Deutschlandticket":
                        estimate = _provisional_price(edge.estimated_distance_km, passengers)
                        edge.estimated_price_eur = estimate["typical_eur"]
                        edge.is_estimate = True
                        edge.metadata.update(estimate)
                        edge.metadata["ticket_coverage"] = "estimated paid German regional fare"
        else:
            domestic, notices, report = result
        for edge in domestic:
            edge.metadata["required_preceding_transfer_minutes"] = minimum
        edges.extend(domestic)
        warnings.extend(notices)
        reports[stage] = report
    known = {node.id for node in origin_nodes + destination_nodes}
    nodes.extend(node for node in gateways_from + gateways_to if node.id not in known)
    return list({node.id: node for node in nodes}.values()), edges, warnings, {
        "enabled": True, "available": True, "corridor": manifest.key,
        "countries": [origin_country, destination_country], "crossing_edges": len(crossings),
        "origin_stations": len(origin_map), "destination_stations": len(destination_map),
        "candidate_limit_per_country": manifest.max_stations_per_country,
        "country_stages": reports,
        "coverage": "SNCF cross-border rail services present in the imported feed; not all operators are guaranteed.",
    }
