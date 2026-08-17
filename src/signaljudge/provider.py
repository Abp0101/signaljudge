"""Hardened The Odds API V4 adapter with schema normalization and bounded retries."""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from signaljudge import __version__
from signaljudge.io import atomic_write_json, load_json
from signaljudge.models import ValidationError, isoformat, parse_datetime


API_BASE_URL = "https://api.the-odds-api.com/v4"
SPORT_CONFIGS = {
    "baseball_mlb": {"default_region": "us", "title": "MLB"},
    "basketball_nba": {"default_region": "us", "title": "NBA"},
    "soccer_epl": {"default_region": "uk", "title": "English Premier League"},
}
ALLOWED_SPORTS = frozenset(SPORT_CONFIGS)
ALLOWED_REGIONS = frozenset({"us", "uk", "eu", "au"})


def _normalize_v4_payload(
    payload: Any, fetched_at: str, response_headers: Mapping[str, str], region: str
) -> Dict[str, Any]:
    """Convert the provider's nested V4 schema into SignalJudge's stable contract."""
    if not isinstance(payload, list) or len(payload) > 5000:
        raise ValidationError("odds provider response must be a bounded event list")
    events: List[Dict[str, Any]] = []
    for event in payload:
        if not isinstance(event, dict):
            raise ValidationError("odds provider returned a non-object event")
        books: List[Dict[str, Any]] = []
        bookmakers = event.get("bookmakers", [])
        if not isinstance(bookmakers, list) or len(bookmakers) > 250:
            raise ValidationError("odds provider returned invalid bookmakers")
        for bookmaker in bookmakers:
            if not isinstance(bookmaker, dict):
                continue
            markets = bookmaker.get("markets", [])
            if not isinstance(markets, list):
                continue
            for market in markets:
                if not isinstance(market, dict) or market.get("key") != "h2h":
                    continue
                books.append(
                    {
                        "book": bookmaker.get("key"),
                        "market": "h2h",
                        "updated_at": market.get("last_update")
                        or bookmaker.get("last_update"),
                        "outcomes": market.get("outcomes"),
                    }
                )
                break
        events.append(
            {
                "event_id": event.get("id"),
                "sport_key": event.get("sport_key"),
                "home_team": event.get("home_team"),
                "away_team": event.get("away_team"),
                "start_time": event.get("commence_time"),
                "books": books,
            }
        )
    return {
        "success": True,
        "source": "the-odds-api-v4",
        "odds_format": "decimal",
        "fetched_at": fetched_at,
        "evaluated_at": fetched_at,
        "data_origin": "LIVE",
        "region": region,
        "cache_age_seconds": 0.0,
        "quota": {
            "remaining": response_headers.get("x-requests-remaining"),
            "used": response_headers.get("x-requests-used"),
            "last_cost": response_headers.get("x-requests-last"),
        },
        "data": events,
    }


class LiveOddsProvider:
    def __init__(
        self,
        cache_dir: Path,
        timeout_seconds: float = 8.0,
        max_attempts: int = 4,
        max_cache_age_seconds: float = 15 * 60,
    ):
        self.cache_dir = cache_dir
        self.timeout_seconds = min(30.0, max(1.0, timeout_seconds))
        self.max_attempts = min(5, max(1, max_attempts))
        self.max_cache_age_seconds = min(60 * 60, max(0.0, max_cache_age_seconds))

    def fetch(
        self, sport_key: str, api_key: Optional[str] = None, region: Optional[str] = None
    ) -> Dict[str, Any]:
        if sport_key not in ALLOWED_SPORTS:
            raise ValidationError(
                f"sport_key must be one of the free-tier allowlist: {', '.join(sorted(ALLOWED_SPORTS))}"
            )
        selected_region = region or str(SPORT_CONFIGS[sport_key]["default_region"])
        if selected_region not in ALLOWED_REGIONS:
            raise ValidationError(
                f"region must be one of: {', '.join(sorted(ALLOWED_REGIONS))}"
            )
        key = (api_key or os.getenv("THE_ODDS_API_KEY", "")).strip()
        if not key or len(key) > 512:
            raise ValidationError("THE_ODDS_API_KEY is missing or invalid")
        # V4 requires apiKey in the query string. Never log request URLs or chain
        # HTTPError objects, because either could disclose the credential.
        query = urllib.parse.urlencode(
            {
                "apiKey": key,
                "regions": selected_region,
                "markets": "h2h",
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            }
        )
        url = f"{API_BASE_URL}/sports/{sport_key}/odds/?{query}"
        cache_path = self.cache_dir / f"{sport_key}-{selected_region}.json"
        headers = {
            "Accept": "application/json",
            "User-Agent": f"SignalJudge/{__version__}",
        }

        last_error: Optional[Exception] = None
        for attempt in range(self.max_attempts):
            request = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    if response.status != 200:
                        raise ValidationError(f"odds provider returned HTTP {response.status}")
                    body = response.read(5 * 1024 * 1024 + 1)
                    if len(body) > 5 * 1024 * 1024:
                        raise ValidationError("odds provider response exceeded size limit")
                    provider_payload = json.loads(body.decode("utf-8"))
                    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    payload = _normalize_v4_payload(
                        provider_payload, timestamp, response.headers, selected_region
                    )
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    atomic_write_json(cache_path, payload)
                    return payload
            except urllib.error.HTTPError as exc:
                if exc.code == 429 or 500 <= exc.code <= 504:
                    last_error = exc
                    if attempt + 1 >= self.max_attempts:
                        break
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                    time.sleep(min(8.0, delay) + random.random() * 0.1)
                    continue
                raise ValidationError(
                    f"odds provider rejected the request with HTTP {exc.code}"
                ) from None
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts:
                    time.sleep(min(8.0, 2**attempt) + random.random() * 0.1)
                    continue
        if cache_path.is_file():
            cached = load_json(cache_path)
            if not isinstance(cached, dict):
                raise ValidationError("odds provider unavailable and cache is invalid") from None
            now = datetime.now(timezone.utc)
            cached_at = parse_datetime(cached.get("fetched_at"), "cache.fetched_at")
            cache_age = max(0.0, (now - cached_at).total_seconds())
            if cache_age > self.max_cache_age_seconds:
                raise ValidationError(
                    "odds provider unavailable and last-known-good cache is too old"
                ) from None
            cached["degraded"] = True
            cached["degraded_reason"] = type(last_error).__name__ if last_error else "provider_failure"
            cached["data_origin"] = "CACHE"
            cached["evaluated_at"] = isoformat(now)
            cached["cache_age_seconds"] = cache_age
            return cached
        raise ValidationError("odds provider unavailable and no last-known-good cache exists") from None
