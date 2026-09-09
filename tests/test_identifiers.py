from __future__ import annotations

import re

from anylist_sdk.identifiers import uuid4_hex, uuid5_hex
from anylist_sdk.transport import AnyListTransport


_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def test_anylist_uuid4_is_compact_lowercase_hex() -> None:
    value = uuid4_hex()
    assert _HEX32.fullmatch(value)
    assert "-" not in value


def test_anylist_uuid5_matches_official_compact_vector() -> None:
    # Official app.js calls uuid5(normalizedSourceName,
    # "6d86f27f66474ca6a540fcf62af29e59") for source smart collections.
    assert (
        uuid5_hex("allrecipes", "6d86f27f66474ca6a540fcf62af29e59")
        == "f4162f7b63975202a148ee6a39ed58ad"
    )


def test_transport_client_identifier_uses_same_compact_uuid_form() -> None:
    transport = AnyListTransport()
    try:
        assert _HEX32.fullmatch(transport.client_id)
    finally:
        # No aiohttp session is allocated until .session is accessed.
        pass
