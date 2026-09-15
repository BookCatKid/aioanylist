from __future__ import annotations

import json
from typing import cast

from ..proto import PB, PBProductLookupResponse, decode
from ..transport import AnyListTransport
from ..types import JSONMapping, PlaceSearchResult


class MapsService:
    """Android-native place search used by location-based list notifications."""

    def __init__(self, transport: AnyListTransport):
        self.transport = transport

    async def place_search(
        self,
        query: str,
        *,
        latitude: float,
        longitude: float,
        radius_meters: int,
    ) -> list[PlaceSearchResult]:
        raw = await self.transport.request(
            "POST",
            "/data/maps/place-search",
            fields={
                "query": query,
                "lat": latitude,
                "lng": longitude,
                "radius": radius_meters,
            },
        )
        data = json.loads(raw or b"{}")
        results: list[PlaceSearchResult] = []
        for value in data.get("results", []):
            location = value["geometry"]["location"]
            results.append(
                PlaceSearchResult(
                    name=str(value["name"]),
                    formatted_address=str(value["formatted_address"]),
                    latitude=float(location["lat"]),
                    longitude=float(location["lng"]),
                )
            )
        return results


class ProductsService:
    """Android-native UPC product lookup."""

    def __init__(self, transport: AnyListTransport):
        self.transport = transport

    async def lookup(self, upc: str) -> PBProductLookupResponse | None:
        if not upc:
            raise ValueError("upc must not be empty")
        raw = await self.transport.request(
            "GET",
            f"/data/product-lookup/{upc}",
            allowed_statuses=(404,),
        )
        response = decode("PBProductLookupResponse", raw)
        assert isinstance(response, PB.PBProductLookupResponse)
        return response if response.HasField("listItem") else None


class NativeConfigService:
    """Android's `/data/version-check` remote configuration endpoint."""

    def __init__(self, transport: AnyListTransport):
        self.transport = transport

    async def get(self) -> JSONMapping:
        raw = await self.transport.request(
            "GET",
            "/data/version-check",
            authenticated=self.transport.tokens is not None,
        )
        return cast(JSONMapping, json.loads(raw or b"{}"))
