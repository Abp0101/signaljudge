"""Hardened live provider adapter with quota-aware caching and bounded retries."""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from signaljudge.io import atomic_write_json, load_json
from signaljudge.models import ValidationError


API_BASE_URL = "https://api.theoddsapi.com"
ALLOWED_SPORTS = {
    "baseball_mlb",
    "basketball_nba",
}
SECRET_HEADER = "x-api-key"


class LiveOddsProvider:
    def __init__(self, cache_dir: Path, timeout_seconds: float = 8.0, max_attempts: int = 4):
        self.cache_dir = cache_dir
        self.timeout_seconds = min(30.0, max(1.0, timeout_seconds))
        self.max_attempts = min(5, max(1, max_attempts))

    def fetch(self, sport_key: str, api_key: Optional[str] = None) -> Dict[str, Any]:
        if sport_key not in ALLOWED_SPORTS:
            raise ValidationError(
                f"sport_key must be one of the free-tier allowlist: {', '.join(sorted(ALLOWED_SPORTS))}"
            )
        key = (api_key or os.getenv("THE_ODDS_API_KEY", "")).strip()
        if not key or len(key) > 512:
            raise ValidationError("THE_ODDS_API_KEY is missing or invalid")
        query = urllib.parse.urlencode(
            {"sport_key": sport_key, "markets": "h2h", "oddsFormat": "decimal"}
        )
        url = f"{API_BASE_URL}/odds/?{query}"
        cache_path = self.cache_dir / f"{sport_key}.json"
        etag_path = self.cache_dir / f"{sport_key}.etag"
        headers = {SECRET_HEADER: key, "Accept": "application/json", "User-Agent": "SignalJudge/1.0"}
        if etag_path.is_file():
            headers["If-None-Match"] = etag_path.read_text(encoding="utf-8").strip()

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
                    payload = json.loads(body.decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValidationError("odds provider response was not an object")
                    from datetime import datetime, timezone

                    payload["fetched_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    payload["odds_format"] = "decimal"
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    atomic_write_json(cache_path, payload)
                    etag = response.headers.get("ETag")
                    if etag:
                        etag_path.write_text(etag, encoding="utf-8")
                    return payload
            except urllib.error.HTTPError as exc:
                if exc.code == 304 and cache_path.is_file():
                    return load_json(cache_path)
                if exc.code == 429 or exc.code in {502, 503, 504}:
                    last_error = exc
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                    time.sleep(min(8.0, delay) + random.random() * 0.1)
                    continue
                raise ValidationError(f"odds provider rejected the request with HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts:
                    time.sleep(min(8.0, 2**attempt) + random.random() * 0.1)
                    continue
        if cache_path.is_file():
            cached = load_json(cache_path)
            cached["degraded"] = True
            cached["degraded_reason"] = type(last_error).__name__ if last_error else "provider_failure"
            return cached
        raise ValidationError("odds provider unavailable and no last-known-good cache exists")

