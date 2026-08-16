"""Odds conversion, bookmaker-margin removal, consensus, and movement analysis."""

from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Mapping, Optional, Tuple

from signaljudge.models import (
    NormalizedMarket,
    Prediction,
    ValidationError,
    clamp,
    parse_datetime,
    require_id,
    require_text,
)


def implied_probability(price: float, odds_format: str) -> float:
    if not math.isfinite(price):
        raise ValidationError("odds price must be finite")
    if odds_format == "decimal":
        if price <= 1.0 or price > 1000.0:
            raise ValidationError("decimal odds must be greater than 1 and at most 1000")
        return 1.0 / price
    if odds_format == "american":
        if price == 0 or abs(price) < 100 or abs(price) > 100000:
            raise ValidationError("American odds must have absolute value of at least 100")
        return 100.0 / (price + 100.0) if price > 0 else (-price) / ((-price) + 100.0)
    raise ValidationError("odds_format must be decimal or american")


def _event_index(snapshot: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    if snapshot.get("success") is not True:
        raise ValidationError("odds response does not indicate success")
    events = snapshot.get("data")
    if not isinstance(events, list) or len(events) > 5000:
        raise ValidationError("odds response data must be a bounded list")
    indexed: Dict[str, Mapping[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict):
            raise ValidationError("each odds event must be an object")
        event_id = require_id(event.get("event_id"), "odds.event_id")
        if event_id in indexed:
            raise ValidationError(f"duplicate odds event_id: {event_id}")
        indexed[event_id] = event
    return indexed


def fetched_at(snapshot: Mapping[str, Any]):
    return parse_datetime(snapshot.get("fetched_at"), "odds.fetched_at")


def evaluated_at(snapshot: Mapping[str, Any]):
    return parse_datetime(
        snapshot.get("evaluated_at") or snapshot.get("fetched_at"), "odds.evaluated_at"
    )


def validate_event_identity(prediction: Prediction, event: Mapping[str, Any]) -> None:
    sport_key = event.get("sport_key")
    if sport_key is not None and require_id(sport_key, "odds.sport_key") != prediction.sport_key:
        raise ValidationError(f"sport mismatch for {prediction.event_id}")
    home = require_text(event.get("home_team"), "odds.home_team")
    away = require_text(event.get("away_team"), "odds.away_team")
    if home != prediction.home_team or away != prediction.away_team:
        raise ValidationError(
            f"event identity mismatch for {prediction.event_id}: "
            f"expected {prediction.away_team} at {prediction.home_team}"
        )
    start_value = event.get("start_time") or event.get("commence_time")
    start = parse_datetime(start_value, "odds.start_time")
    if abs((start - prediction.commence_time).total_seconds()) > 15 * 60:
        raise ValidationError(f"event start time mismatch for {prediction.event_id}")


def _book_probabilities(
    event: Mapping[str, Any], prediction: Prediction, snapshot_time, odds_format: str
) -> Tuple[List[Tuple[str, float, float]], int]:
    books = event.get("books") or event.get("bookmakers")
    if not isinstance(books, list) or len(books) > 250:
        raise ValidationError(f"books must be a bounded list for {prediction.event_id}")
    probabilities: List[Tuple[str, float, float]] = []
    seen = set()
    for book in books:
        if not isinstance(book, dict):
            continue
        market = book.get("market")
        if market != "h2h":
            continue
        book_name = require_id(book.get("book") or book.get("key"), "book")
        if book_name in seen:
            continue
        updated = parse_datetime(book.get("updated_at") or book.get("last_update"), "book.updated_at")
        clock_delta = (snapshot_time - updated).total_seconds()
        if clock_delta < -5 * 60:
            continue
        age = max(0.0, clock_delta)
        outcomes = book.get("outcomes")
        if not isinstance(outcomes, list) or not 2 <= len(outcomes) <= 3:
            continue
        raw: Dict[str, float] = {}
        try:
            for outcome in outcomes:
                if not isinstance(outcome, dict):
                    raise ValidationError("outcome must be an object")
                name = require_text(outcome.get("name"), "outcome.name")
                raw[name] = implied_probability(float(outcome.get("price")), odds_format)
        except (TypeError, ValueError, ValidationError):
            continue
        if prediction.selection not in raw or len(raw) != len(outcomes):
            continue
        overround = sum(raw.values())
        if not 0.8 <= overround <= 1.4:
            continue
        probabilities.append((book_name, raw[prediction.selection] / overround, age))
        seen.add(book_name)
    return probabilities, len(books)


def _reject_outliers(rows: List[Tuple[str, float, float]]):
    if len(rows) < 4:
        return rows, []
    values = [row[1] for row in rows]
    centre = statistics.median(values)
    mad = statistics.median([abs(value - centre) for value in values])
    threshold = max(0.06, 3.0 * mad)
    kept = [row for row in rows if abs(row[1] - centre) <= threshold]
    rejected = [row[0] for row in rows if abs(row[1] - centre) > threshold]
    return (kept or rows), rejected


def normalize_market(
    snapshot: Mapping[str, Any],
    prediction: Prediction,
    previous: Optional[NormalizedMarket] = None,
    event_index: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> NormalizedMarket:
    events = event_index if event_index is not None else _event_index(snapshot)
    event = events.get(prediction.event_id)
    if event is None:
        raise ValidationError(f"no live odds matched prediction {prediction.event_id}")
    validate_event_identity(prediction, event)
    snapshot_time = evaluated_at(snapshot)
    odds_format = snapshot.get("odds_format", "decimal")
    if odds_format not in {"decimal", "american"}:
        raise ValidationError("odds_format must be decimal or american")
    rows, total_books = _book_probabilities(event, prediction, snapshot_time, odds_format)
    if not rows:
        raise ValidationError(f"no valid h2h bookmaker prices for {prediction.event_id}")
    kept, rejected = _reject_outliers(rows)
    probabilities = [row[1] for row in kept]
    ages = [row[2] for row in kept]
    consensus = statistics.median(probabilities)
    dispersion = statistics.pstdev(probabilities) if len(probabilities) > 1 else 0.0
    median_age = statistics.median(ages)
    coverage = min(1.0, len(kept) / 5.0)
    freshness = statistics.mean([0.5 ** (age / 900.0) for age in ages])
    dispersion_quality = max(0.0, 1.0 - dispersion / 0.12)
    outlier_penalty = max(0.6, 1.0 - len(rejected) / max(1.0, len(rows) * 2.0))
    source_degraded = snapshot.get("degraded") is True
    degraded_penalty = 0.65 if source_degraded else 1.0
    quality = clamp(
        (0.45 * coverage + 0.30 * freshness + 0.25 * dispersion_quality)
        * outlier_penalty
        * degraded_penalty
    )
    per_book = {row[0]: row[1] for row in kept}

    movement = 0.0
    coherence = 0.0
    if previous is not None:
        movement = consensus - previous.probability
        common_deltas = [
            probability - previous.per_book[book]
            for book, probability in per_book.items()
            if book in previous.per_book
        ]
        if abs(movement) >= 0.02 and common_deltas:
            direction = 1 if movement > 0 else -1
            coherent = sum(1 for delta in common_deltas if direction * delta >= 0.01)
            coherence = coherent / len(common_deltas)

    try:
        cache_age_seconds = max(0.0, float(snapshot.get("cache_age_seconds", 0.0)))
    except (TypeError, ValueError) as exc:
        raise ValidationError("cache_age_seconds must be numeric") from exc

    return NormalizedMarket(
        event_id=prediction.event_id,
        selection=prediction.selection,
        probability=clamp(consensus),
        quality=quality,
        valid_book_count=len(kept),
        total_book_count=total_books,
        rejected_books=rejected,
        dispersion=dispersion,
        median_age_seconds=median_age,
        stale=median_age > 30 * 60,
        per_book=per_book,
        movement=movement,
        movement_coherence=coherence,
        source_degraded=source_degraded,
        data_origin=str(snapshot.get("data_origin", "FIXTURE")),
        cache_age_seconds=cache_age_seconds,
    )


def normalize_all(
    snapshot: Mapping[str, Any],
    predictions: List[Prediction],
    previous_snapshot: Optional[Mapping[str, Any]] = None,
) -> Tuple[Dict[str, NormalizedMarket], List[str]]:
    markets, warnings, _ = normalize_all_detailed(snapshot, predictions, previous_snapshot)
    return markets, warnings


def normalize_all_detailed(
    snapshot: Mapping[str, Any],
    predictions: List[Prediction],
    previous_snapshot: Optional[Mapping[str, Any]] = None,
) -> Tuple[Dict[str, NormalizedMarket], List[str], Dict[str, str]]:
    markets: Dict[str, NormalizedMarket] = {}
    warnings: List[str] = []
    errors: Dict[str, str] = {}
    current_events = _event_index(snapshot)
    previous_events: Optional[Dict[str, Mapping[str, Any]]] = None
    use_previous = previous_snapshot is not None
    if previous_snapshot is not None:
        try:
            if evaluated_at(previous_snapshot) >= evaluated_at(snapshot):
                warnings.append("previous odds snapshot was not older than the current snapshot; movement ignored")
                use_previous = False
            else:
                previous_events = _event_index(previous_snapshot)
        except ValidationError as exc:
            warnings.append(f"previous odds snapshot ignored: {exc}")
            use_previous = False
    for prediction in predictions:
        previous = None
        if use_previous and previous_events is not None and prediction.event_id in previous_events:
            try:
                previous = normalize_market(
                    previous_snapshot, prediction, event_index=previous_events  # type: ignore[arg-type]
                )
            except ValidationError as exc:
                warnings.append(f"previous market ignored for {prediction.event_id}: {exc}")
        try:
            markets[prediction.event_id] = normalize_market(
                snapshot, prediction, previous, event_index=current_events
            )
        except ValidationError as exc:
            message = str(exc)
            warnings.append(message)
            errors[prediction.event_id] = message
    return markets, warnings, errors
