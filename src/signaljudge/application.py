"""Localhost application API for browsing live fixtures and reconciled rankings."""

from __future__ import annotations

import errno
import json
import os
import sys
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from signaljudge.io import load_json, load_predictions
from signaljudge.models import (
    Decision,
    Prediction,
    RunResult,
    ValidationError,
    isoformat,
    parse_datetime,
    require_id,
    require_text,
)
from signaljudge.provider import ALLOWED_REGIONS, ALLOWED_SPORTS, SPORT_CONFIGS, LiveOddsProvider
from signaljudge.service import ReconciliationService
from signaljudge.state import StateStore
from signaljudge.web_assets import APP_CSS, APP_HTML, APP_JS


class RefreshRateLimitError(ValidationError):
    """Raised when a manual provider refresh is attempted too frequently."""


@dataclass(frozen=True)
class ApplicationConfig:
    prediction_dir: Path
    db_path: Path
    cache_dir: Path
    demo_dir: Path
    response_ttl_seconds: float = 5 * 60
    refresh_cooldown_seconds: float = 30


class ApplicationService:
    """Coordinates provider calls, prediction files, reconciliation, and view models."""

    def __init__(
        self,
        config: ApplicationConfig,
        provider: Optional[LiveOddsProvider] = None,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self.provider = provider or LiveOddsProvider(config.cache_dir)
        self.monotonic = monotonic
        self._responses: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}
        self._last_provider_call: Dict[Tuple[str, str], float] = {}
        self._lock = threading.RLock()

    def sports(self) -> Dict[str, Any]:
        sports = []
        for key in sorted(ALLOWED_SPORTS):
            path = self._prediction_path(key)
            status = "missing"
            count = 0
            if path.is_file():
                try:
                    predictions = self._load_sport_predictions(key)
                    status = "ready"
                    count = len(predictions)
                except ValidationError:
                    status = "invalid"
            sports.append(
                {
                    "key": key,
                    "title": str(SPORT_CONFIGS[key]["title"]),
                    "default_region": str(SPORT_CONFIGS[key]["default_region"]),
                    "prediction_status": status,
                    "prediction_count": count,
                }
            )
        return {
            "sports": sports,
            "regions": sorted(ALLOWED_REGIONS),
            "default_sport": "soccer_epl",
            "live_configured": bool(os.getenv("THE_ODDS_API_KEY", "").strip()),
        }

    def live_rankings(
        self, sport_key: str, region: Optional[str] = None, force_refresh: bool = False
    ) -> Dict[str, Any]:
        if sport_key not in ALLOWED_SPORTS:
            raise ValidationError(f"unsupported sport: {sport_key}")
        selected_region = region or str(SPORT_CONFIGS[sport_key]["default_region"])
        if selected_region not in ALLOWED_REGIONS:
            raise ValidationError(f"unsupported bookmaker region: {selected_region}")
        cache_key = (sport_key, selected_region)
        with self._lock:
            now = self.monotonic()
            cached = self._responses.get(cache_key)
            if cached and not force_refresh and now - cached[0] < self.config.response_ttl_seconds:
                payload = dict(cached[1])
                payload["response_cache"] = True
                payload["response_cache_age_seconds"] = round(max(0.0, now - cached[0]), 1)
                return payload
            last_call = self._last_provider_call.get(cache_key)
            if force_refresh and last_call is not None:
                remaining = self.config.refresh_cooldown_seconds - (now - last_call)
                if remaining > 0:
                    raise RefreshRateLimitError(
                        f"refresh available in {max(1, int(remaining + 0.999))} seconds"
                    )
            self._last_provider_call[cache_key] = now
            snapshot = self.provider.fetch(sport_key, region=selected_region)
            predictions, prediction_status = self._prediction_source(sport_key)
            payload = self._build_live_payload(
                sport_key, selected_region, snapshot, predictions, prediction_status
            )
            payload["response_cache"] = False
            payload["response_cache_age_seconds"] = 0.0
            self._responses[cache_key] = (self.monotonic(), payload)
            return dict(payload)

    def demo_rankings(self) -> Dict[str, Any]:
        predictions = load_predictions(self.config.demo_dir / "model_predictions.json")
        opening_snapshot = load_json(self.config.demo_dir / "odds_opening.json")
        latest_snapshot = load_json(self.config.demo_dir / "odds_latest.json")
        with self._lock, StateStore(self.config.db_path) as store:
            service = ReconciliationService(store)
            opening = service.run(
                predictions, opening_snapshot, mode="application-demo-opening"
            )
            result = service.run(
                predictions,
                latest_snapshot,
                mode="application-demo-latest",
                previous_snapshot=opening_snapshot,
            )
            audit_valid, audit_entries = store.verify_audit_chain()
        matches = [self._decision_view(item) for item in result.decisions]
        return self._result_payload(
            title="Reproducible assessment demo",
            sport_key="baseball_mlb",
            region="fixture",
            fetched_at=result.odds_fetched_at,
            result=result,
            matches=matches,
            total_events=len(matches),
            prediction_status="demo",
            predictions_loaded=len(predictions),
            unmatched_predictions=0,
            data_origin="DEMO",
            degraded=False,
            cache_age_seconds=0.0,
            quota={},
            audit_valid=audit_valid,
            audit_entries=audit_entries,
        )

    def _build_live_payload(
        self,
        sport_key: str,
        region: str,
        snapshot: Mapping[str, Any],
        predictions: List[Prediction],
        prediction_status: str,
    ) -> Dict[str, Any]:
        events = snapshot.get("data")
        if not isinstance(events, list):
            raise ValidationError("normalized odds snapshot is missing its event list")
        valid_events: List[Dict[str, Any]] = []
        invalid_event_count = 0
        for event in events:
            if not isinstance(event, dict):
                invalid_event_count += 1
                continue
            try:
                normalized_event = dict(event)
                normalized_event["event_id"] = require_id(
                    event.get("event_id"), "odds.event_id"
                )
                normalized_event["sport_key"] = require_id(
                    event.get("sport_key"), "odds.sport_key"
                )
                if normalized_event["sport_key"] != sport_key:
                    raise ValidationError("provider event returned for the wrong sport")
                normalized_event["home_team"] = require_text(
                    event.get("home_team"), "odds.home_team"
                )
                normalized_event["away_team"] = require_text(
                    event.get("away_team"), "odds.away_team"
                )
                start_value = event.get("start_time") or event.get("commence_time")
                normalized_event["start_time"] = isoformat(
                    parse_datetime(start_value, "odds.start_time")
                )
            except ValidationError:
                invalid_event_count += 1
                continue
            valid_events.append(normalized_event)
        event_ids = {
            str(event.get("event_id"))
            for event in valid_events
        }
        candidates = [item for item in predictions if item.event_id in event_ids]
        unmatched_predictions = len(predictions) - len(candidates)
        result: Optional[RunResult] = None
        decisions: Dict[str, Decision] = {}
        with StateStore(self.config.db_path) as store:
            if candidates:
                result = ReconciliationService(store).run(
                    candidates, snapshot, mode="application-live"
                )
                decisions = {item.event_id: item for item in result.decisions}
            audit_valid, audit_entries = store.verify_audit_chain()

        matches: List[Dict[str, Any]] = []
        for event in valid_events:
            event_id = event.get("event_id")
            if not isinstance(event_id, str):
                continue
            decision = decisions.get(event_id)
            if decision is not None:
                view = self._decision_view(decision)
            else:
                view = self._unpredicted_event_view(event, snapshot)
            matches.append(view)
        matches.sort(key=self._match_sort_key)

        payload = self._result_payload(
            title=str(SPORT_CONFIGS[sport_key]["title"]),
            sport_key=sport_key,
            region=region,
            fetched_at=str(snapshot.get("fetched_at", "")) or None,
            result=result,
            matches=matches,
            total_events=len(matches),
            prediction_status=prediction_status,
            predictions_loaded=len(predictions),
            unmatched_predictions=unmatched_predictions,
            data_origin=str(snapshot.get("data_origin", "UNKNOWN")),
            degraded=bool(snapshot.get("degraded", False)),
            cache_age_seconds=float(snapshot.get("cache_age_seconds", 0.0) or 0.0),
            quota=snapshot.get("quota") if isinstance(snapshot.get("quota"), dict) else {},
            audit_valid=audit_valid,
            audit_entries=audit_entries,
        )
        if invalid_event_count:
            payload["warnings"].append(
                f"{invalid_event_count} malformed provider event(s) were omitted from the application view"
            )
        return payload

    @staticmethod
    def _result_payload(
        *,
        title: str,
        sport_key: str,
        region: str,
        fetched_at: Optional[str],
        result: Optional[RunResult],
        matches: List[Dict[str, Any]],
        total_events: int,
        prediction_status: str,
        predictions_loaded: int,
        unmatched_predictions: int,
        data_origin: str,
        degraded: bool,
        cache_age_seconds: float,
        quota: Mapping[str, Any],
        audit_valid: bool,
        audit_entries: int,
    ) -> Dict[str, Any]:
        source_counts = result.source_counts if result else {"MODEL": 0, "MARKET": 0, "ABSTAIN": 0}
        return {
            "title": title,
            "sport_key": sport_key,
            "region": region,
            "fetched_at": fetched_at,
            "run_id": result.run_id if result else None,
            "reused": result.reused if result else False,
            "total_events": total_events,
            "reconciled_events": sum(
                1 for item in matches if item.get("status") == "RECONCILED"
            ),
            "material_conflicts": result.material_conflicts if result else 0,
            "source_counts": source_counts,
            "warnings": list(result.warnings) if result else [],
            "matches": matches,
            "prediction_source": {
                "status": prediction_status,
                "loaded": predictions_loaded,
                "unmatched": unmatched_predictions,
            },
            "market_source": {
                "origin": data_origin,
                "degraded": degraded,
                "cache_age_seconds": round(cache_age_seconds, 1),
                "quota": dict(quota),
            },
            "audit": {"valid": audit_valid, "entries": audit_entries},
        }

    @staticmethod
    def _decision_view(decision: Decision) -> Dict[str, Any]:
        data = decision.as_dict()
        data["prediction_available"] = True
        data["event_state"] = ApplicationService._event_state(decision.commence_time)
        return data

    @staticmethod
    def _unpredicted_event_view(
        event: Mapping[str, Any], snapshot: Mapping[str, Any]
    ) -> Dict[str, Any]:
        commence_time = event.get("start_time") or event.get("commence_time")
        books = event.get("books") if isinstance(event.get("books"), list) else []
        return {
            "event_id": str(event.get("event_id", "unknown")),
            "sport_key": str(event.get("sport_key", "")),
            "commence_time": str(commence_time or ""),
            "home_team": str(event.get("home_team", "Unknown home team")),
            "away_team": str(event.get("away_team", "Unknown away team")),
            "selection": None,
            "model_probability": None,
            "market_probability": None,
            "reconciled_probability": None,
            "winner": "UNAVAILABLE",
            "decision_confidence": None,
            "material_conflict": False,
            "final_rank": None,
            "rank_delta": None,
            "movement": None,
            "reason_codes": ["MODEL_PREDICTION_UNAVAILABLE"],
            "rationale": (
                "No independent model prediction matched this provider event. "
                "The fixture remains visible and SignalJudge does not derive a model score from its odds."
            ),
            "status": "NO_PREDICTION",
            "prediction_available": False,
            "book_count": len(books),
            "event_state": ApplicationService._event_state(str(commence_time or "")),
            "data_origin": str(snapshot.get("data_origin", "UNKNOWN")),
        }

    @staticmethod
    def _match_sort_key(item: Mapping[str, Any]) -> Tuple[int, float, str]:
        rank = item.get("final_rank")
        rank_value = int(rank) if isinstance(rank, int) else 1_000_000
        try:
            kickoff = parse_datetime(item.get("commence_time"), "commence_time").timestamp()
        except ValidationError:
            kickoff = float("inf")
        return rank_value, kickoff, str(item.get("event_id", ""))

    @staticmethod
    def _event_state(commence_time: str) -> str:
        try:
            start = parse_datetime(commence_time, "commence_time")
        except ValidationError:
            return "UNKNOWN"
        return "STARTED" if start <= datetime.now(timezone.utc) else "UPCOMING"

    def _prediction_path(self, sport_key: str) -> Path:
        return self.config.prediction_dir / f"{sport_key}.json"

    def _load_sport_predictions(self, sport_key: str) -> List[Prediction]:
        predictions = load_predictions(self._prediction_path(sport_key))
        if any(item.sport_key != sport_key for item in predictions):
            raise ValidationError(
                f"{sport_key}.json may contain only {sport_key} predictions"
            )
        return predictions

    def _prediction_source(self, sport_key: str) -> Tuple[List[Prediction], str]:
        path = self._prediction_path(sport_key)
        if not path.is_file():
            return [], "missing"
        return self._load_sport_predictions(sport_key), "ready"


class ApplicationHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: Tuple[str, int], service: ApplicationService):
        super().__init__(address, ApplicationRequestHandler)
        self.application_service = service


class ApplicationRequestHandler(BaseHTTPRequestHandler):
    server_version = "SignalJudge/1.3"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if not self._trusted_host():
            self._json_error(HTTPStatus.BAD_REQUEST, "untrusted Host header")
            return
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/":
            self._send(HTTPStatus.OK, "text/html; charset=utf-8", APP_HTML.encode("utf-8"))
            return
        if parsed.path == "/assets/app.css":
            self._send(HTTPStatus.OK, "text/css; charset=utf-8", APP_CSS.encode("utf-8"))
            return
        if parsed.path == "/assets/app.js":
            self._send(
                HTTPStatus.OK,
                "text/javascript; charset=utf-8",
                APP_JS.encode("utf-8"),
            )
            return
        if parsed.path == "/favicon.ico":
            self._send(HTTPStatus.NO_CONTENT, "image/x-icon", b"")
            return
        if parsed.path == "/api/sports":
            self._json(HTTPStatus.OK, self._service.sports())
            return
        if parsed.path in {"/api/rankings", "/api/demo"}:
            if self.headers.get("X-SignalJudge-Request") != "1":
                self._json_error(HTTPStatus.FORBIDDEN, "missing same-origin application header")
                return
            try:
                if parsed.path == "/api/demo":
                    payload = self._service.demo_rankings()
                else:
                    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
                    sport_key = self._one(query, "sport", "soccer_epl")
                    region = self._one(query, "region", None)
                    refresh = self._one(query, "refresh", "0") == "1"
                    payload = self._service.live_rankings(sport_key, region, refresh)
                self._json(HTTPStatus.OK, payload)
            except RefreshRateLimitError as exc:
                self._json_error(HTTPStatus.TOO_MANY_REQUESTS, str(exc))
            except ValidationError as exc:
                self._json_error(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc))
            except Exception as exc:
                print(f"application request failed: {type(exc).__name__}", file=sys.stderr)
                self._json_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "unexpected application error; inspect the local terminal",
                )
            return
        self._json_error(HTTPStatus.NOT_FOUND, "route not found")

    @property
    def _service(self) -> ApplicationService:
        return self.server.application_service  # type: ignore[attr-defined]

    @staticmethod
    def _one(
        query: Mapping[str, List[str]], key: str, default: Optional[str]
    ) -> Optional[str]:
        values = query.get(key)
        if not values:
            return default
        if len(values) != 1 or len(values[0]) > 128:
            raise ValidationError(f"query parameter {key} must have one bounded value")
        return values[0]

    def _trusted_host(self) -> bool:
        host = self.headers.get("Host", "")
        name = host.rsplit(":", 1)[0].strip("[]").lower()
        return name in {"127.0.0.1", "localhost", "::1"}

    def _json(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", body)

    def _json_error(self, status: HTTPStatus, message: str) -> None:
        self._json(status, {"error": message, "status": int(status)})

    def _send(
        self,
        status: HTTPStatus,
        content_type: str,
        body: bytes,
        cache: bool = False,
    ) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'none'",
        )
        self.send_header("Cache-Control", "public, max-age=3600" if cache else "no-store")
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def log_message(self, format: str, *args: object) -> None:
        # Do not log URLs or query strings. Provider credentials never enter this API,
        # but suppressing request targets keeps that invariant robust as routes evolve.
        return


def serve_application(
    service: ApplicationService, port: int = 8765, open_browser: bool = False
) -> None:
    if not 1024 <= port <= 65535:
        raise ValidationError("port must be between 1024 and 65535")
    try:
        server = ApplicationHTTPServer(("127.0.0.1", port), service)
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.EADDRINUSE:
            raise ValidationError(
                f"port {port} is already in use; stop the older server or choose --port {port + 1}"
            ) from None
        raise ValidationError(f"could not bind the localhost application on port {port}") from None
    url = f"http://127.0.0.1:{port}/"
    print(f"SignalJudge application: {url} (Ctrl-C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
