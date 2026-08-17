"""Validated, odds-independent local rating-model inference."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from signaljudge.io import load_json
from signaljudge.models import (
    Prediction,
    ValidationError,
    clamp,
    parse_datetime,
    require_id,
    require_probability,
    require_text,
)


@dataclass(frozen=True)
class Fixture:
    event_id: str
    sport_key: str
    commence_time: datetime
    home_team: str
    away_team: str

    @classmethod
    def from_event(cls, event: Mapping[str, Any]) -> "Fixture":
        return cls(
            event_id=require_id(event.get("event_id"), "fixture.event_id"),
            sport_key=require_id(event.get("sport_key"), "fixture.sport_key"),
            commence_time=parse_datetime(
                event.get("start_time") or event.get("commence_time"),
                "fixture.commence_time",
            ),
            home_team=require_text(event.get("home_team"), "fixture.home_team"),
            away_team=require_text(event.get("away_team"), "fixture.away_team"),
        )


@dataclass(frozen=True)
class ModelMetrics:
    accuracy: float
    calibration_error: float
    sample_size: int
    brier: float
    log_loss: float


@dataclass(frozen=True)
class RatingModel:
    sport_key: str
    model_version: str
    trained_at: datetime
    training_cutoff: str
    outcome_mode: str
    ratings: Dict[str, float]
    appearances: Dict[str, int]
    aliases: Dict[str, str]
    home_advantage: float
    draw_base: float
    draw_scale: float
    minimum_draw: float
    maximum_draw: float
    minimum_team_games: int
    metrics: ModelMetrics
    sources: List[Dict[str, Any]]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RatingModel":
        if payload.get("schema_version") != 1 or payload.get("model_type") != "elo_rating":
            raise ValidationError("model artifact must be an elo_rating schema version 1")
        sport_key = require_id(payload.get("sport_key"), "model.sport_key")
        model_version = require_id(payload.get("model_version"), "model.model_version")
        trained_at = parse_datetime(payload.get("trained_at"), "model.trained_at")
        training_cutoff = require_text(payload.get("training_cutoff"), "model.training_cutoff", 64)
        outcome_mode = payload.get("outcome_mode")
        if outcome_mode not in {"binary", "three_way"}:
            raise ValidationError("model.outcome_mode must be binary or three_way")

        raw_ratings = payload.get("ratings")
        raw_appearances = payload.get("appearances")
        raw_aliases = payload.get("aliases", {})
        if not isinstance(raw_ratings, dict) or not 2 <= len(raw_ratings) <= 1000:
            raise ValidationError("model.ratings must contain between 2 and 1000 teams")
        if not isinstance(raw_appearances, dict) or not isinstance(raw_aliases, dict):
            raise ValidationError("model appearances and aliases must be objects")
        ratings: Dict[str, float] = {}
        appearances: Dict[str, int] = {}
        for raw_name, raw_rating in raw_ratings.items():
            name = require_text(raw_name, "model.rating.team")
            try:
                rating = float(raw_rating)
                games = int(raw_appearances.get(raw_name, 0))
            except (TypeError, ValueError) as exc:
                raise ValidationError("model team rating or appearances is invalid") from exc
            if not math.isfinite(rating) or not 500.0 <= rating <= 2500.0:
                raise ValidationError("model team rating is outside the allowed range")
            if not 0 <= games <= 100_000:
                raise ValidationError("model team appearances is outside the allowed range")
            ratings[name] = rating
            appearances[name] = games
        aliases: Dict[str, str] = {}
        if len(raw_aliases) > 1000:
            raise ValidationError("model aliases exceeds the allowed range")
        for raw_alias, raw_target in raw_aliases.items():
            alias = require_text(raw_alias, "model.alias")
            target = require_text(raw_target, "model.alias.target")
            if target not in ratings:
                raise ValidationError(f"model alias target is unknown: {target}")
            aliases[alias] = target

        parameters = payload.get("parameters")
        raw_metrics = payload.get("validation")
        sources = payload.get("sources")
        if not isinstance(parameters, dict) or not isinstance(raw_metrics, dict):
            raise ValidationError("model parameters and validation metrics are required")
        if not isinstance(sources, list) or not 1 <= len(sources) <= 20:
            raise ValidationError("model must declare bounded training sources")

        def bounded_number(name: str, low: float, high: float) -> float:
            try:
                value = float(parameters[name])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationError(f"model parameter {name} is invalid") from exc
            if not math.isfinite(value) or not low <= value <= high:
                raise ValidationError(f"model parameter {name} is outside the allowed range")
            return value

        try:
            sample_size = int(raw_metrics["sample_size"])
            brier = float(raw_metrics["brier"])
            log_loss = float(raw_metrics["log_loss"])
            minimum_team_games = int(parameters.get("minimum_team_games", 10))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("model validation metrics are invalid") from exc
        accuracy = require_probability(raw_metrics.get("accuracy"), "model.validation.accuracy")
        calibration_error = float(raw_metrics.get("calibration_error", 0.5))
        if not 1 <= sample_size <= 10_000_000:
            raise ValidationError("model validation sample size is outside the allowed range")
        if not 0.0 <= calibration_error <= 0.5:
            raise ValidationError("model calibration error is outside the allowed range")
        if not math.isfinite(brier) or not 0.0 <= brier <= 2.0:
            raise ValidationError("model Brier score is outside the allowed range")
        if not math.isfinite(log_loss) or not 0.0 <= log_loss <= 50.0:
            raise ValidationError("model log loss is outside the allowed range")
        if not 1 <= minimum_team_games <= 1000:
            raise ValidationError("model minimum_team_games is outside the allowed range")

        safe_sources: List[Dict[str, Any]] = []
        for source in sources:
            if not isinstance(source, dict):
                raise ValidationError("model training source must be an object")
            safe_sources.append(
                {
                    "url": require_text(source.get("url"), "model.source.url", 500),
                    "sha256": require_id(source.get("sha256"), "model.source.sha256"),
                    "rows": int(source.get("rows", 0)),
                    "fields_used": list(source.get("fields_used", [])),
                }
            )

        return cls(
            sport_key=sport_key,
            model_version=model_version,
            trained_at=trained_at,
            training_cutoff=training_cutoff,
            outcome_mode=outcome_mode,
            ratings=ratings,
            appearances=appearances,
            aliases=aliases,
            home_advantage=bounded_number("home_advantage", -200.0, 300.0),
            draw_base=bounded_number("draw_base", 0.05, 0.60),
            draw_scale=bounded_number("draw_scale", 100.0, 10_000.0),
            minimum_draw=bounded_number("minimum_draw", 0.01, 0.50),
            maximum_draw=bounded_number("maximum_draw", 0.01, 0.60),
            minimum_team_games=minimum_team_games,
            metrics=ModelMetrics(
                accuracy=accuracy,
                calibration_error=calibration_error,
                sample_size=sample_size,
                brier=brier,
                log_loss=log_loss,
            ),
            sources=safe_sources,
        )

    @classmethod
    def load(cls, path: Path) -> "RatingModel":
        payload = load_json(path)
        if not isinstance(payload, dict):
            raise ValidationError("model artifact must be a JSON object")
        return cls.from_dict(payload)

    def predict(self, fixtures: List[Fixture], generated_at: datetime) -> List[Prediction]:
        predictions: List[Prediction] = []
        for fixture in fixtures:
            if fixture.sport_key != self.sport_key or generated_at >= fixture.commence_time:
                continue
            home_key = self.aliases.get(fixture.home_team, fixture.home_team)
            away_key = self.aliases.get(fixture.away_team, fixture.away_team)
            home_known = home_key in self.ratings
            away_known = away_key in self.ratings
            home_rating = self.ratings.get(home_key, 1500.0)
            away_rating = self.ratings.get(away_key, 1500.0)
            rating_gap = home_rating + self.home_advantage - away_rating
            home_without_draw = 1.0 / (1.0 + 10.0 ** (-rating_gap / 400.0))
            if self.outcome_mode == "three_way":
                draw_probability = clamp(
                    self.draw_base - abs(rating_gap) / self.draw_scale,
                    self.minimum_draw,
                    self.maximum_draw,
                )
                outcome_probabilities = {
                    fixture.home_team: (1.0 - draw_probability) * home_without_draw,
                    "Draw": draw_probability,
                    fixture.away_team: (1.0 - draw_probability) * (1.0 - home_without_draw),
                }
            else:
                outcome_probabilities = {
                    fixture.home_team: home_without_draw,
                    fixture.away_team: 1.0 - home_without_draw,
                }
            selection, probability = max(
                outcome_probabilities.items(), key=lambda item: (item[1], item[0])
            )
            home_games = self.appearances.get(home_key, 0)
            away_games = self.appearances.get(away_key, 0)
            out_of_distribution = (
                not home_known
                or not away_known
                or home_games < self.minimum_team_games
                or away_games < self.minimum_team_games
            )
            predictions.append(
                Prediction(
                    event_id=fixture.event_id,
                    sport_key=fixture.sport_key,
                    commence_time=fixture.commence_time,
                    home_team=fixture.home_team,
                    away_team=fixture.away_team,
                    selection=selection,
                    model_probability=clamp(probability),
                    historical_accuracy=self.metrics.accuracy,
                    historical_sample_size=self.metrics.sample_size,
                    calibration_error=self.metrics.calibration_error,
                    generated_at=generated_at,
                    model_version=self.model_version,
                    out_of_distribution=out_of_distribution,
                )
            )
        return predictions

    def metadata(self) -> Dict[str, Any]:
        return {
            "type": "trained_local_model",
            "model_version": self.model_version,
            "trained_at": self.trained_at.isoformat(),
            "training_cutoff": self.training_cutoff,
            "accuracy": self.metrics.accuracy,
            "calibration_error": self.metrics.calibration_error,
            "sample_size": self.metrics.sample_size,
            "brier": self.metrics.brier,
            "log_loss": self.metrics.log_loss,
            "team_count": len(self.ratings),
        }


def model_path(model_dir: Path, sport_key: str) -> Path:
    return model_dir / f"{sport_key}.model.json"


def load_model_if_available(model_dir: Path, sport_key: str) -> Optional[RatingModel]:
    path = model_path(model_dir, sport_key)
    if not path.is_file():
        return None
    model = RatingModel.load(path)
    if model.sport_key != sport_key:
        raise ValidationError(f"model artifact {path.name} has the wrong sport_key")
    return model
