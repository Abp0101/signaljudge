"""Validated domain models with no unsafe deserialization or runtime dependency."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional


ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class ValidationError(ValueError):
    """Raised when untrusted input fails schema or range validation."""


def parse_datetime(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValidationError(f"{field_name} must be an ISO-8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValidationError(f"{field_name} is not valid ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValidationError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def require_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ValidationError(f"{field_name} contains invalid characters or length")
    return value


def require_text(value: Any, field_name: str, max_length: int = 120) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be text")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > max_length or any(ord(ch) < 32 for ch in cleaned):
        raise ValidationError(f"{field_name} is empty, too long, or contains controls")
    return cleaned


def require_probability(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValidationError(f"{field_name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} must be numeric") from exc
    if not math.isfinite(number) or not 0.0 < number < 1.0:
        raise ValidationError(f"{field_name} must be strictly between 0 and 1")
    return number


def clamp(value: float, low: float = 0.001, high: float = 0.999) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class Prediction:
    event_id: str
    sport_key: str
    commence_time: datetime
    home_team: str
    away_team: str
    selection: str
    model_probability: float
    historical_accuracy: float
    historical_sample_size: int
    calibration_error: float
    generated_at: datetime
    model_version: str
    out_of_distribution: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Prediction":
        try:
            sample_size = int(data["historical_sample_size"])
            calibration_error = float(data.get("calibration_error", 0.05))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("historical metrics are missing or invalid") from exc
        if not 1 <= sample_size <= 10_000_000:
            raise ValidationError("historical_sample_size is outside the allowed range")
        if not 0.0 <= calibration_error <= 0.5:
            raise ValidationError("calibration_error must be between 0 and 0.5")
        prediction = cls(
            event_id=require_id(data.get("event_id"), "event_id"),
            sport_key=require_id(data.get("sport_key"), "sport_key"),
            commence_time=parse_datetime(data.get("commence_time"), "commence_time"),
            home_team=require_text(data.get("home_team"), "home_team"),
            away_team=require_text(data.get("away_team"), "away_team"),
            selection=require_text(data.get("selection"), "selection"),
            model_probability=require_probability(data.get("model_probability"), "model_probability"),
            historical_accuracy=require_probability(data.get("historical_accuracy"), "historical_accuracy"),
            historical_sample_size=sample_size,
            calibration_error=calibration_error,
            generated_at=parse_datetime(data.get("generated_at"), "generated_at"),
            model_version=require_id(data.get("model_version"), "model_version"),
            out_of_distribution=bool(data.get("out_of_distribution", False)),
        )
        if prediction.selection not in {prediction.home_team, prediction.away_team}:
            raise ValidationError("selection must exactly match home_team or away_team")
        return prediction


@dataclass(frozen=True)
class BookProbability:
    bookmaker: str
    probability: float
    age_seconds: float


@dataclass(frozen=True)
class NormalizedMarket:
    event_id: str
    selection: str
    probability: float
    quality: float
    valid_book_count: int
    total_book_count: int
    rejected_books: List[str]
    dispersion: float
    median_age_seconds: float
    stale: bool
    per_book: Dict[str, float]
    movement: float = 0.0
    movement_coherence: float = 0.0


@dataclass
class Decision:
    event_id: str
    selection: str
    model_probability: float
    market_probability: float
    reconciled_probability: float
    model_reliability: float
    market_reliability: float
    model_weight: float
    market_weight: float
    winner: str
    decision_confidence: float
    material_conflict: bool
    model_rank: int
    market_rank: int
    rank_delta: int
    movement: float
    movement_coherence: float
    reason_codes: List[str]
    rationale: str
    status: str = "RECONCILED"
    final_rank: int = 0
    previous_probability: Optional[float] = None
    previous_winner: Optional[str] = None
    decision_id: Optional[int] = None
    previous_decision_id: Optional[int] = None
    audit_hash: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunResult:
    run_id: str
    mode: str
    odds_fetched_at: str
    decisions: List[Decision]
    material_conflicts: int
    source_counts: Dict[str, int]
    reused: bool = False
    warnings: List[str] = field(default_factory=list)

