from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

from .exceptions import TagDataError
from .transport import AnyListTransport

_REQUIRED_KEYS = {
    "tags",
    "normalizedDisplayNamesIndex",
    "impliedTags",
    "autocompleteKeywords",
}


@dataclass(slots=True, frozen=True)
class TagData:
    language: str
    tags: dict[str, Any]
    normalized_display_names_index: dict[str, Any]
    implied_tags: dict[str, list[str]]
    tag_keywords_index: dict[str, list[str]]
    autocomplete_keywords: dict[str, Any]

    @classmethod
    def from_json(cls, language: str, value: dict[str, Any]) -> TagData:
        missing = _REQUIRED_KEYS.difference(value)
        # AnyList's localized tag-data files may omit tagKeywordsIndex. app.js only uses
        # that index for the English stemming/classification path; non-English classification
        # uses normalizedDisplayNamesIndex and separately loads the English resource as a
        # fallback. The English resource therefore still requires tagKeywordsIndex.
        if language == "en" and "tagKeywordsIndex" not in value:
            missing.add("tagKeywordsIndex")
        if missing:
            raise TagDataError(f"AnyList tag data is missing keys: {sorted(missing)!r}")
        return cls(
            language=language,
            tags=dict(value["tags"]),
            normalized_display_names_index=dict(value["normalizedDisplayNamesIndex"]),
            implied_tags={k: list(v) for k, v in value["impliedTags"].items()},
            tag_keywords_index={k: list(v) for k, v in value.get("tagKeywordsIndex", {}).items()},
            autocomplete_keywords=dict(value["autocompleteKeywords"]),
        )


class TagDataManager:
    """Loads the same /static/webapp/data/tag_data*.json resources as AnyList Web."""

    SUPPORTED_LANGUAGES = frozenset({"en", "de"})

    def __init__(
        self,
        transport: AnyListTransport,
        *,
        locale: str = "en-US",
        cache_dir: str | Path | None = None,
        cache_ttl: float = 24 * 60 * 60,
        allow_stale: bool = True,
    ) -> None:
        self.transport = transport
        self.locale = locale
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.cache_ttl = cache_ttl
        self.allow_stale = allow_stale
        self._loaded: dict[str, TagData] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def language(self) -> str:
        locale = self.locale.replace("_", "-").lower()
        language = locale.split("-", 1)[0]
        return language if language in self.SUPPORTED_LANGUAGES else "en"

    @staticmethod
    def path_for_language(language: str) -> str:
        return (
            "/static/webapp/data/tag_data.json"
            if language == "en"
            else f"/static/webapp/data/tag_data_{language}.json"
        )

    def _cache_path(self, language: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / f"tag_data_{language}.json"

    async def _load_cache(self, language: str) -> tuple[TagData | None, float]:
        path = self._cache_path(language)
        if path is None:
            return None, 0.0

        def read_cached() -> tuple[str, float]:
            stat = path.stat()
            return path.read_text("utf-8"), stat.st_mtime

        try:
            raw, mtime = await asyncio.to_thread(read_cached)
            return TagData.from_json(language, json.loads(raw)), mtime
        except (OSError, ValueError, TypeError, KeyError, TagDataError):
            return None, 0.0

    async def _save_cache(self, language: str, raw: str) -> None:
        path = self._cache_path(language)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, raw, "utf-8")

    async def get(self, language: str | None = None, *, refresh: bool = False) -> TagData:
        language = language or self.language
        if language not in self.SUPPORTED_LANGUAGES:
            language = "en"
        if not refresh and language in self._loaded:
            return self._loaded[language]
        lock = self._locks.setdefault(language, asyncio.Lock())
        async with lock:
            if not refresh and language in self._loaded:
                return self._loaded[language]
            cached, mtime = await self._load_cache(language)
            if cached is not None and not refresh and time.time() - mtime < self.cache_ttl:
                self._loaded[language] = cached
                return cached
            url = f"{self.transport.base_url}{self.path_for_language(language)}"
            try:
                async with self.transport.session.get(url) as response:
                    if response.status >= 400:
                        raise TagDataError(
                            f"AnyList tag data {language!r} returned HTTP {response.status}"
                        )
                    raw = await response.text()
                parsed = TagData.from_json(language, json.loads(raw))
                self._loaded[language] = parsed
                await self._save_cache(language, raw)
                return parsed
            except (aiohttp.ClientError, ValueError, TagDataError) as exc:
                if cached is not None and self.allow_stale:
                    self._loaded[language] = cached
                    return cached
                if isinstance(exc, TagDataError):
                    raise
                raise TagDataError(f"Unable to load AnyList tag data for {language}") from exc

    async def active_and_english(self) -> tuple[TagData, TagData]:
        active = await self.get(self.language)
        english = active if active.language == "en" else await self.get("en")
        return active, english
