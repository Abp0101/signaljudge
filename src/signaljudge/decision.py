"""Deterministic, explainable reliability-weighted decision engine."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from signaljudge.models import Decision, NormalizedMarket, Prediction, clamp, isoformat


@dataclass(frozen=True)
class DecisionConfig:
    policy_version: str = "2.0"
    material_probability_gap: float = 0.10
    material_rank_delta: int = 3
    coherent_movement_threshold: float = 0.08
    coherent_book_fraction: float = 0.60
    minimum_books: int = 2
    minimum_source_reliability: float = 0.25


def model_reliability(prediction: Prediction, as_of: datetime) -> float:
    sample_factor = min(1.0, 0.55 + 0.45 * prediction.historical_sample_size / 250.0)
    age_hours = max(0.0, (as_of - prediction.generated_at).total_seconds() / 3600.0)
    freshness = max(0.65, 1.0 - 0.003 * age_hours)
    distribution_factor = 0.48 if prediction.out_of_distribution else 1.0
    return clamp(
        prediction.historical_accuracy
        * sample_factor
        * freshness
        * (1.0 - prediction.calibration_error)
        * distribution_factor
    )


def _human_rationale(codes: List[str], winner: str) -> str:
    explanations = {
        "MATERIAL_PROBABILITY_GAP": "the model and fair market probability differ materially",
        "MATERIAL_RANK_GAP": "the sources place this event far apart in the batch ranking",
        "COHERENT_MARKET_MOVEMENT": "the price moved materially in the same direction across several bookmakers",
        "MARKET_STALE": "the available bookmaker prices are stale",
        "MARKET_OUTLIERS_REMOVED": "isolated bookmaker outliers were removed before consensus",
        "MODEL_OUT_OF_DISTRIBUTION": "the prediction is outside the model's validated operating distribution",
        "MODEL_HIGHER_RELIABILITY": "the calibrated model has the stronger reliability score",
        "MARKET_HIGHER_RELIABILITY": "the fresh cross-book consensus has the stronger reliability score",
        "INSUFFICIENT_MARKET_COVERAGE": "too few valid bookmakers support the market signal",
        "MARKET_SOURCE_DEGRADED": "the provider is using degraded cached market data",
        "MARKET_UNAVAILABLE": "no valid matching market evidence is available",
        "BOTH_SOURCES_UNRELIABLE": "neither source meets the minimum reliability policy",
        "SOURCES_BROADLY_AGREE": "the two probability estimates broadly agree",
    }
    details = "; ".join(explanations[code] for code in codes if code in explanations)
    if winner == "ABSTAIN":
        return f"Abstained because {details}."
    return f"{winner.title()} won because {details}."


def unresolved_decision(
    prediction: Prediction,
    as_of: datetime,
    model_rank: int,
    reason: str,
    previous: Optional[Decision] = None,
) -> Decision:
    codes = ["MARKET_UNAVAILABLE"]
    return Decision(
        event_id=prediction.event_id,
        selection=prediction.selection,
        model_probability=prediction.model_probability,
        market_probability=None,
        reconciled_probability=None,
        model_reliability=model_reliability(prediction, as_of),
        market_reliability=0.0,
        model_weight=0.0,
        market_weight=0.0,
        winner="ABSTAIN",
        decision_confidence=0.0,
        material_conflict=False,
        model_rank=model_rank,
        market_rank=None,
        rank_delta=None,
        movement=0.0,
        movement_coherence=0.0,
        reason_codes=codes,
        rationale=f"Abstained because no valid matching market evidence is available: {reason}.",
        sport_key=prediction.sport_key,
        commence_time=isoformat(prediction.commence_time),
        home_team=prediction.home_team,
        away_team=prediction.away_team,
        status="UNRESOLVED",
        previous_probability=previous.reconciled_probability if previous else None,
        previous_winner=previous.winner if previous else None,
    )


def reconcile(
    prediction: Prediction,
    market: NormalizedMarket,
    as_of: datetime,
    model_rank: int,
    market_rank: int,
    previous: Optional[Decision] = None,
    config: DecisionConfig = DecisionConfig(),
) -> Decision:
    probability_gap = abs(prediction.model_probability - market.probability)
    rank_delta = abs(model_rank - market_rank)
    material = probability_gap >= config.material_probability_gap or rank_delta >= config.material_rank_delta
    model_score = model_reliability(prediction, as_of)
    market_score = clamp(0.72 * market.quality)
    model_weight = model_score
    market_weight = market_score
    codes: List[str] = []

    if probability_gap >= config.material_probability_gap:
        codes.append("MATERIAL_PROBABILITY_GAP")
    if rank_delta >= config.material_rank_delta:
        codes.append("MATERIAL_RANK_GAP")
    if market.rejected_books:
        codes.append("MARKET_OUTLIERS_REMOVED")
    if market.source_degraded:
        codes.append("MARKET_SOURCE_DEGRADED")

    forced_winner: Optional[str] = None
    market_unusable = market.valid_book_count < config.minimum_books or market.stale
    model_unusable = prediction.out_of_distribution
    if market.valid_book_count < config.minimum_books:
        codes.append("INSUFFICIENT_MARKET_COVERAGE")
    if market.stale:
        codes.append("MARKET_STALE")
    if prediction.out_of_distribution:
        codes.append("MODEL_OUT_OF_DISTRIBUTION")

    if (market_unusable and model_unusable) or (
        model_score < config.minimum_source_reliability
        and market_score < config.minimum_source_reliability
    ):
        forced_winner = "ABSTAIN"
        codes.append("BOTH_SOURCES_UNRELIABLE")
    elif market.valid_book_count < config.minimum_books:
        forced_winner = "MODEL"
    elif market.stale:
        forced_winner = "MODEL"
    elif prediction.out_of_distribution:
        forced_winner = "MARKET"
    elif (
        abs(market.movement) >= config.coherent_movement_threshold
        and market.movement_coherence >= config.coherent_book_fraction
    ):
        forced_winner = "MARKET"
        codes.append("COHERENT_MARKET_MOVEMENT")

    if forced_winner == "ABSTAIN":
        return Decision(
            event_id=prediction.event_id,
            selection=prediction.selection,
            model_probability=prediction.model_probability,
            market_probability=market.probability,
            reconciled_probability=None,
            model_reliability=model_score,
            market_reliability=market_score,
            model_weight=0.0,
            market_weight=0.0,
            winner="ABSTAIN",
            decision_confidence=0.0,
            material_conflict=material,
            model_rank=model_rank,
            market_rank=market_rank,
            rank_delta=rank_delta,
            movement=market.movement,
            movement_coherence=market.movement_coherence,
            reason_codes=codes,
            rationale=_human_rationale(codes, "ABSTAIN"),
            sport_key=prediction.sport_key,
            commence_time=isoformat(prediction.commence_time),
            home_team=prediction.home_team,
            away_team=prediction.away_team,
            status="ABSTAINED",
            previous_probability=previous.reconciled_probability if previous else None,
            previous_winner=previous.winner if previous else None,
        )
    if forced_winner == "MODEL":
        model_weight *= 2.25
        market_weight *= 0.40
    elif forced_winner == "MARKET":
        market_weight *= 2.25
        model_weight *= 0.40

    winner = "MODEL" if model_weight >= market_weight else "MARKET"
    if forced_winner is None:
        codes.append("MODEL_HIGHER_RELIABILITY" if winner == "MODEL" else "MARKET_HIGHER_RELIABILITY")
    if not material:
        codes.append("SOURCES_BROADLY_AGREE")

    total_weight = model_weight + market_weight
    final_probability = clamp(
        (model_weight * prediction.model_probability + market_weight * market.probability) / total_weight
    )
    winner_weight = model_weight if winner == "MODEL" else market_weight
    decision_confidence = winner_weight / total_weight

    return Decision(
        event_id=prediction.event_id,
        selection=prediction.selection,
        model_probability=prediction.model_probability,
        market_probability=market.probability,
        reconciled_probability=final_probability,
        model_reliability=model_score,
        market_reliability=market_score,
        model_weight=model_weight,
        market_weight=market_weight,
        winner=winner,
        decision_confidence=decision_confidence,
        material_conflict=material,
        model_rank=model_rank,
        market_rank=market_rank,
        rank_delta=rank_delta,
        movement=market.movement,
        movement_coherence=market.movement_coherence,
        reason_codes=codes,
        rationale=_human_rationale(codes, winner),
        sport_key=prediction.sport_key,
        commence_time=isoformat(prediction.commence_time),
        home_team=prediction.home_team,
        away_team=prediction.away_team,
        previous_probability=previous.reconciled_probability if previous else None,
        previous_winner=previous.winner if previous else None,
    )
