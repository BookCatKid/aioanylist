from __future__ import annotations

import json
import os
import time

import pytest
from aiohttp import web

from anylist_sdk.exceptions import TagDataError
from anylist_sdk.tag_data import TagData, TagDataManager
from anylist_sdk.transport import AnyListTransport
from test_transport import server


def payload(tag: str = "milk") -> dict:
    return {
        "tags": {tag: {"displayName": tag.title()}},
        "normalizedDisplayNamesIndex": {tag: tag},
        "impliedTags": {},
        "tagKeywordsIndex": {tag: [tag]},
        "autocompleteKeywords": {tag[0]: {tag: [[tag, 1]]}},
    }


def test_language_and_paths_match_web_client() -> None:
    class T:
        base_url = "https://www.anylist.com"

    for locale, expected in [("en-US", "en"), ("de_DE", "de"), ("fr-FR", "en")]:
        manager = TagDataManager(T(), locale=locale)
        assert manager.language == expected
    assert TagDataManager.path_for_language("en") == "/static/webapp/data/tag_data.json"
    assert TagDataManager.path_for_language("de") == "/static/webapp/data/tag_data_de.json"


def test_tag_data_rejects_missing_official_keys() -> None:
    with pytest.raises(TagDataError):
        TagData.from_json("en", {"tags": {}})


def test_german_tag_data_allows_missing_english_keyword_index() -> None:
    german = payload("milch")
    german.pop("tagKeywordsIndex")
    parsed = TagData.from_json("de", german)
    assert parsed.language == "de"
    assert parsed.tag_keywords_index == {}

    english = payload("milk")
    english.pop("tagKeywordsIndex")
    with pytest.raises(TagDataError):
        TagData.from_json("en", english)


@pytest.mark.asyncio
async def test_active_and_english_loads_german_plus_english() -> None:
    paths = []

    async def handler(request):
        paths.append(request.path)
        return web.json_response(payload("milch" if request.path.endswith("_de.json") else "milk"))

    app = web.Application()
    app.router.add_get("/static/webapp/data/tag_data.json", handler)
    app.router.add_get("/static/webapp/data/tag_data_de.json", handler)
    async with server(app) as base:
        async with AnyListTransport(base_url=base) as transport:
            active, english = await TagDataManager(transport, locale="de-DE").active_and_english()
    assert active.language == "de" and english.language == "en"
    assert paths == ["/static/webapp/data/tag_data_de.json", "/static/webapp/data/tag_data.json"]


@pytest.mark.asyncio
async def test_fresh_disk_cache_avoids_network(tmp_path) -> None:
    path = tmp_path / "tag_data_en.json"
    path.write_text(json.dumps(payload()), "utf-8")

    class Session:
        def get(self, url):
            raise AssertionError("network should not be used")

    class T:
        base_url = "https://www.anylist.com"
        session = Session()

    value = await TagDataManager(T(), cache_dir=tmp_path).get("en")
    assert "milk" in value.tags


@pytest.mark.asyncio
async def test_stale_cache_falls_back_when_official_resource_fails(tmp_path) -> None:
    path = tmp_path / "tag_data_en.json"
    path.write_text(json.dumps(payload()), "utf-8")
    old = time.time() - 100
    os.utime(path, (old, old))

    async def handler(request):
        return web.Response(status=503)

    app = web.Application()
    app.router.add_get("/static/webapp/data/tag_data.json", handler)
    async with server(app) as base:
        async with AnyListTransport(base_url=base) as transport:
            value = await TagDataManager(
                transport, cache_dir=tmp_path, cache_ttl=0.01, allow_stale=True
            ).get("en")
    assert "milk" in value.tags
