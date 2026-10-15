"""Enrich airport candidates with public IATA and ICAO identifiers.

OpenStreetMap normally supplies these tags. This small Wikidata fallback is used
only when an otherwise valid airport lacks a code, allowing flight search to
survive an Overpass outage without relying on a hard-coded airport list.
"""

import re

import httpx

WIKIDATA_ENTITIES_URL = "https://www.wikidata.org/w/api.php"
_IATA_PATTERN = re.compile(r"^[A-Z]{3}$")
_ICAO_PATTERN = re.compile(r"^[A-Z]{4}$")


def _claim_value(entity: dict, property_id: str) -> str | None:
    """Extract a simple string value from the first usable Wikidata statement."""
    for claim in entity.get("claims", {}).get(property_id, []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(value, str):
            return value.upper()
    return None


async def enrich_airport_codes(airports: list[dict]) -> list[dict]:
    """Fill missing airport identifiers from Wikidata while preserving source data.

    Network errors deliberately leave the original candidates unchanged: airport
    discovery remains useful even when the optional enrichment service is down.
    """
    unresolved_ids = sorted({
        airport.get("wikidata")
        for airport in airports
        if isinstance(airport.get("wikidata"), str)
        and not airport.get("iata")
    })
    if not unresolved_ids:
        return airports
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                WIKIDATA_ENTITIES_URL,
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(unresolved_ids),
                    "props": "claims",
                    "format": "json",
                },
                headers={"User-Agent": "TRO-Travel-Route-Optimizer/0.1"},
            )
            response.raise_for_status()
            entities = response.json().get("entities", {})
    except (httpx.HTTPError, ValueError):
        return airports

    for airport in airports:
        entity = entities.get(airport.get("wikidata"), {})
        iata = _claim_value(entity, "P238")
        icao = _claim_value(entity, "P239")
        if not airport.get("iata") and iata and _IATA_PATTERN.fullmatch(iata):
            airport["iata"] = iata
            airport["code_source"] = "wikidata"
        if not airport.get("icao") and icao and _ICAO_PATTERN.fullmatch(icao):
            airport["icao"] = icao
            airport.setdefault("code_source", "wikidata")
    return airports
