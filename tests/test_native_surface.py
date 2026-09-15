from __future__ import annotations

import pytest

from aioanylist.proto import PB, encode
from aioanylist.services.http_api import AlexaService, PhotosService
from aioanylist.services.native import (
    MapsService,
    NativeConfigService,
    ProductsService,
)


@pytest.mark.asyncio
async def test_android_native_search_lookup_and_config_contracts(fake_transport) -> None:
    photos = PhotosService(fake_transport)
    maps = MapsService(fake_transport)
    products = ProductsService(fake_transport)
    config = NativeConfigService(fake_transport)

    fake_transport.responses.extend(
        [
            b'{"results":[{"MediaUrl":"https://img/full.jpg","Thumbnail":{"MediaUrl":"https://img/thumb.jpg"}}]}',
            b'{"results":[{"name":"Store","formatted_address":"1 Main St","geometry":{"location":{"lat":32.1,"lng":-117.2}}}]}',
            (
                200,
                encode(
                    PB.PBProductLookupResponse(
                        listItem=PB.ListItem(
                            identifier="product-item",
                            name="Milk",
                            productUpc="012345678905",
                        ),
                        productThumbnailUrl="https://img/product.jpg",
                    )
                ),
            ),
            (404, b""),
            (200, encode(PB.PBProductLookupResponse())),
            b'{"should_hide_alexa_linking_ui":true}',
        ]
    )

    images = await photos.image_search("milk")
    places = await maps.place_search("market", latitude=32.1, longitude=-117.2, radius_meters=1500)
    product = await products.lookup("012345678905")
    missing = await products.lookup("000000000000")
    empty = await products.lookup("111111111111")
    remote_config = await config.get()

    assert images[0].media_url == "https://img/full.jpg"
    assert images[0].thumbnail_url == "https://img/thumb.jpg"
    assert places[0].name == "Store"
    assert places[0].formatted_address == "1 Main St"
    assert places[0].latitude == 32.1
    assert places[0].longitude == -117.2
    assert product is not None and product.listItem.name == "Milk"
    assert product.productThumbnailUrl == "https://img/product.jpg"
    assert missing is None
    assert empty is None
    assert remote_config == {"should_hide_alexa_linking_ui": True}

    assert fake_transport.calls == [
        ("POST /data/photos/image-search", {"query": "milk"}, None),
        (
            "POST /data/maps/place-search",
            {"query": "market", "lat": 32.1, "lng": -117.2, "radius": 1500},
            None,
        ),
        ("GET /data/product-lookup/012345678905", {}, None),
        ("GET /data/product-lookup/000000000000", {}, None),
        ("GET /data/product-lookup/111111111111", {}, None),
        ("GET /data/version-check", {}, None),
    ]


@pytest.mark.asyncio
async def test_android_native_alexa_default_list_contract(fake_transport) -> None:
    alexa = AlexaService(fake_transport)
    fake_transport.responses.append(b'{"success":true}')

    assert await alexa.set_default_list_id("list") == {"success": True}
    assert fake_transport.calls == [
        ("POST /data/alexa/set-default-list-id", {"list_id": "list"}, None),
    ]
