"""Protect exact French parent resolution through cache and graph creation."""
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.database.session import Base
from backend.database.models import RailStationRecord, RailStationMembershipRecord
from backend.database.repository import CityNodeRepository
from backend.rail.france_connection_provider import OpenTripPlannerFranceRailProvider, FranceRailRoutingError
from backend.services.node_factory import create_city_transport_nodes


class FranceStopAreaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.city = dict(source_id="paris", city_name="Paris", name="Paris", latitude=48.845, longitude=2.373, source="test")
        self.station = dict(id="gtfs:fr-national-rail:FR:paris", name="Paris Gare de Lyon", latitude=48.845, longitude=2.373, type="train_station", source="sncf-gtfs", distance_km=0, gtfs_stop_ids={"fr-national-rail": "StopArea:OCE87686006"})

    async def test_cached_hub_keeps_parent_and_resolves_without_name_search(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine, expire_on_commit=False)
        now = datetime.now()
        with sessions() as session:
            station = RailStationRecord(canonical_id="FR:paris", home_region="FR", name=self.station["name"], normalized_name="paris", latitude=48.845, longitude=2.373, created_at=now, updated_at=now)
            session.add(station)
            session.flush()
            session.add(RailStationMembershipRecord(station_id=station.id, network_key="fr-national-rail", source_stop_id="StopArea:OCE87686006", source_name=station.name, latitude=48.845, longitude=2.373, is_parent_station=True, service_score=4, imported_at=now))
            session.commit()
        with patch("backend.database.repository.SessionLocal", sessions):
            repository = CityNodeRepository()
            repository.save_infrastructure(self.city, {"train_stations": [self.station], "airports": []})
            cached = repository.get_infrastructure("paris")
        self.assertEqual(cached["train_stations"][0]["gtfs_stop_ids"], self.station["gtfs_stop_ids"])
        node = create_city_transport_nodes(self.city, cached, "origin")[1]
        provider = OpenTripPlannerFranceRailProvider()
        provider._query = AsyncMock(return_value={"data": {"feeds": [{"feedId": "0"}]}})
        for _ in range(2):
            self.assertEqual(await provider._station_location(node), {"stopLocation": {"stopLocationId": "0:StopArea:OCE87686006"}})
        provider._query.assert_awaited_once_with("query { feeds { feedId } }")
        engine.dispose()

    async def test_registry_node_without_parent_does_not_choose_operator_stop(self):
        self.station.pop("gtfs_stop_ids")
        node = create_city_transport_nodes(self.city, {"train_stations": [self.station], "airports": []}, "origin")[1]
        provider = OpenTripPlannerFranceRailProvider()
        provider._query = AsyncMock()
        with self.assertRaises(FranceRailRoutingError):
            await provider._station_location(node)
        provider._query.assert_not_awaited()

