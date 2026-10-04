from __future__ import annotations

import threading
from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import httpx

from app.config.settings import Settings
from app.integrations.secrets import resolve_secret, scrub
from app.providers.base import ProviderNotConfigured
from app.schemas.domain import NewsItem

NEWS_URL = "https://api.benzinga.com/api/v2/news"
# One publisher. Below the official-source threshold, so a Benzinga article
# alone cannot satisfy the two-source rule.
BENZINGA_CREDIBILITY = Decimal("0.75")
PAGE_SIZE = 50


class BenzingaError(RuntimeError):
    pass


class _FeedState:
    def __init__(self) -> None:
        self.articles: dict[str, NewsItem] = {}
        self.cursors: dict[str, int] = {}
        self.authenticated: bool | None = None
        self.last_success: str | None = None
        self.last_fetch: str | None = None
        self.last_error: str | None = None


_state = _FeedState()
_lock = threading.Lock()


def reset_feed_state() -> None:
    global _state
    with _lock:
        _state = _FeedState()


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _names(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    found: list[str] = []
    for item in raw:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = str(item.get("name") or "")
        else:
            continue
        cleaned = name.strip().upper()
        if cleaned and cleaned not in found:
            found.append(cleaned)
    return tuple(found)


def _channel_names(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    found: list[str] = []
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
        else:
            continue
        if name and name not in found:
            found.append(name)
    return tuple(found)


def _label(channels: tuple[str, ...]) -> str:
    if not channels:
        return "news"
    return channels[0].strip().lower().replace(" ", "_")[:40] or "news"


def _importance(article: dict) -> int:
    try:
        return max(0, int(article.get("importance_rank")))
    except (TypeError, ValueError):
        return 1


def _content(article: dict) -> str:
    text = article.get("teaser") or article.get("body") or ""
    if not isinstance(text, str):
        return ""
    return text[:2000]


def _map_article(article: dict) -> NewsItem | None:
    if not isinstance(article, dict):
        return None
    article_id = str(article.get("id") or "").strip()
    title = article.get("title")
    url = article.get("url")
    created = _parse_time(article.get("created"))
    if not article_id or not isinstance(title, str) or not title.strip():
        return None
    if not isinstance(url, str) or not url.startswith("http"):
        return None
    if created is None:
        return None
    updated = _parse_time(article.get("updated")) or created
    channels = _channel_names(article.get("channels"))
    author = article.get("author")
    return NewsItem(
        source="benzinga",
        url=url,
        headline=title.strip(),
        published_at=created,
        symbols=_names(article.get("stocks")),
        category=_label(channels),
        event_type=_label(channels),
        importance=_importance(article),
        content=_content(article),
        source_credibility=BENZINGA_CREDIBILITY,
        observed_at=updated,
        article_id=article_id,
        author=author.strip() if isinstance(author, str) else "",
        source_host=urlparse(url).netloc.lower(),
        channels=channels,
    )


def _cursor_key(symbols: tuple[str, ...] | None) -> str:
    if not symbols:
        return "*"
    return ",".join(symbols)


def _chunks(symbols: tuple[str, ...], size: int = 50) -> list[tuple[str, ...]]:
    return [symbols[index : index + size] for index in range(0, len(symbols), size)]


class BenzingaNewsProvider:
    name = "benzinga"

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport

    def status_detail(self) -> dict:
        with _lock:
            return {
                "provider": self.name,
                "authenticated": _state.authenticated,
                "last_success": _state.last_success,
                "last_fetch": _state.last_fetch,
                "article_count": len(_state.articles),
                "last_error": _state.last_error,
            }

    def load_news(self, as_of: datetime, symbols: tuple[str, ...] | None = None) -> list[NewsItem]:
        key, _origin = resolve_secret(self.settings, "benzinga_api_key")
        if not key:
            raise ProviderNotConfigured("BENZINGA_API_KEY")
        normalized = tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols or () if symbol.strip()))
        if symbols is not None and not normalized:
            return self._visible(as_of)
        query_key = _cursor_key(normalized or None)
        with _lock:
            cursor = _state.cursors.get(query_key)
        batches = _chunks(normalized) if normalized else [()]
        collected: list[dict] = []
        for batch in batches:
            collected.extend(self._fetch(key, batch, cursor))
        mapped: dict[str, NewsItem] = {}
        newest = cursor or 0
        for raw in collected:
            item = _map_article(raw)
            if item is None:
                continue
            mapped[item.article_id] = item
            newest = max(newest, int(item.observed_at.timestamp()))
        now = _iso(datetime.now(timezone.utc))
        with _lock:
            _state.articles.update(mapped)
            if mapped or collected == []:
                _state.cursors[query_key] = newest
            _state.authenticated = True
            _state.last_success = now
            _state.last_fetch = now
            _state.last_error = None
        return self._visible(as_of)

    def _fetch(self, key: str, symbols: tuple[str, ...], cursor: int | None) -> list[dict]:
        params: dict[str, str | int] = {
            "token": key,
            "pageSize": PAGE_SIZE,
            "displayOutput": "abstract",
        }
        if symbols:
            # primaryTickers is the filter this key actually honors. tickers= returned an empty page.
            params["primaryTickers"] = ",".join(symbols)
        if cursor:
            params["updatedSince"] = cursor
        fetched_at = _iso(datetime.now(timezone.utc))
        try:
            with httpx.Client(transport=self.transport, timeout=20) as client:
                response = client.get(
                    NEWS_URL,
                    params=params,
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            self._fail(fetched_at, "Benzinga לא זמין", key, scrub(str(exc), [key]))
            raise BenzingaError("Benzinga לא זמין") from exc
        if response.status_code in {401, 403}:
            self._fail(fetched_at, "האימות נכשל או שאין הרשאה", key, authenticated=False)
            raise BenzingaError("האימות נכשל או שאין הרשאה")
        if response.status_code != 200:
            self._fail(fetched_at, f"Benzinga החזיר {response.status_code}", key)
            raise BenzingaError(f"Benzinga החזיר {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            self._fail(fetched_at, "Benzinga החזיר תשובה לא תקינה", key)
            raise BenzingaError("Benzinga החזיר תשובה לא תקינה") from exc
        if not isinstance(payload, list):
            self._fail(fetched_at, "Benzinga החזיר תשובה לא תקינה", key)
            raise BenzingaError("Benzinga החזיר תשובה לא תקינה")
        return [item for item in payload if isinstance(item, dict)]

    def _fail(
        self,
        fetched_at: str,
        message: str,
        key: str,
        detail: str | None = None,
        authenticated: bool | None = None,
    ) -> None:
        safe = scrub(detail or message, [key])
        with _lock:
            _state.last_fetch = fetched_at
            _state.last_error = safe or message
            if authenticated is False:
                _state.authenticated = False

    def _visible(self, as_of: datetime) -> list[NewsItem]:
        with _lock:
            rows = list(_state.articles.values())
        visible = [item for item in rows if item.published_at <= as_of and item.observed_at <= as_of]
        visible.sort(key=lambda item: item.published_at, reverse=True)
        return visible
