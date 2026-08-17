"""Deterministic, explainable reliability-weighted decision engine."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

from signaljudge.models import Decision, NormalizedMarket, Prediction, clamp, isoformat


@dataclass(frozen=True)
class DecisionConfig:
    policy_version: str = "2.2"
    material_probability_gap: float = 0.10
    material_rank_delta: int = 3
    coherent_movement_threshold: float = 0.08
    coherent_book_fraction: float = 0.60
    minimum_books: int = 2
    minimum_source_reliability: float = 0.25
    market_quality_ceiling: float = 0.72
    model_data_half_life_days: float = 365.0
    minimum_model_data_freshness: float = 0.65
    model_data_age_notice_days: float = 30.0
    sample_size_reference: int = 250
    sample_reliability_floor: float = 0.55
    inference_freshness_floor: float = 0.65
    inference_decay_per_hour: float = 0.003
    out_of_distribution_multiplier: float = 0.48
    forced_winner_multiplier: float = 2.25
    forced_loser_multiplier: float = 0.40


@dataclass(frozen=True)
class SourceAssessment:
    rank_delta: int
    material: bool
    model_score: float
    market_score: float
    forced_winner: Optional[str]
    reason_codes: Tuple[str, ...]


def model_reliability(
    prediction: Prediction,
    as_of: datetime,
    config: DecisionConfig = DecisionConfig(),
) -> float:
    sample_factor = min(
        1.0,
        config.sample_reliability_floor
        + (1.0 - config.sample_reliability_floor)
        * prediction.historical_sample_size
        / config.sample_size_reference,
    )
    age_hours = max(0.0, (as_of - prediction.generated_at).total_seconds() / 3600.0)
    inference_freshness = max(
        config.inference_freshness_floor,
        1.0 - config.inference_decay_per_hour * age_hours,
    )
    source_age_days = max(
        0.0,
        (as_of - (prediction.source_data_at or prediction.generated_at)).total_seconds()
        / 86_400.0,
    )
    data_freshness = max(
        config.minimum_model_data_freshness,
        0.5 ** (source_age_days / config.model_data_half_life_days),
    )
    distribution_factor = (
        config.out_of_distribution_multiplier if prediction.out_of_distribution else 1.0
    )
    return clamp(
        prediction.historical_accuracy
        * sample_factor
        * inference_freshness
        * data_freshness
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
        "MODEL_SOURCE_DATA_AGED": "the model's source data is older than the policy notice threshold",
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
    config: DecisionConfig = DecisionConfig(),
) -> Decision:
    codes = ["MARKET_UNAVAILABLE"]
    return Decision(
        event_id=prediction.event_id,
        selection=prediction.selection,
        model_probability=prediction.model_probability,
        market_probability=None,
        reconciled_probability=None,
        model_reliability=model_reliability(prediction, as_of, config),
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
        reason_codes=tuple(codes),
        rationale=f"Abstained because no valid matching market evidence is available: {reason}.",
        sport_key=prediction.sport_key,
        commence_time=isoformat(prediction.commence_time),
        home_team=prediction.home_team,
        away_team=prediction.away_team,
        status="UNRESOLVED",
        previous_probability=previous.reconciled_probability if previous else None,
        previous_winner=previous.winner if previous else None,
    )


def _assess_sources(
    prediction: Prediction,
    market: NormalizedMarket,
    as_of: datetime,
    model_rank: int,
    market_rank: int,
    config: DecisionConfig,
) -> SourceAssessment:
    probability_gap = abs(prediction.model_probability - market.probability)
    rank_delta = abs(model_rank - market_rank)
    material = (
        probability_gap >= config.material_probability_gap
        or rank_delta >= config.material_rank_delta
    )
    model_score = model_reliability(prediction, as_of, config)
    market_score = clamp(config.market_quality_ceiling * market.quality)
    codes: List[str] = []

    if probability_gap >= config.material_probability_gap:
        codes.append("MATERIAL_PROBABILITY_GAP")
    if rank_delta >= config.material_rank_delta:
        codes.append("MATERIAL_RANK_GAP")
    if market.rejected_books:
        codes.append("MARKET_OUTLIERS_REMOVED")
    if market.source_degraded:
        codes.append("MARKET_SOURCE_DEGRADED")
    if prediction.source_data_at is not None:
        source_age_days = max(
            0.0, (as_of - prediction.source_data_at).total_seconds() / 86_400.0
        )
        if source_age_days >= config.model_data_age_notice_days:
            codes.append("MODEL_SOURCE_DATA_AGED")

    market_unusable = market.valid_book_count < config.minimum_books or market.stale
    model_unusable = prediction.out_of_distribution
    if market.valid_book_count < config.minimum_books:
        codes.append("INSUFFICIENT_MARKET_COVERAGE")
    if market.stale:
        codes.append("MARKET_STALE")
    if prediction.out_of_distribution:
        codes.append("MODEL_OUT_OF_DISTRIBUTION")

    forced_winner: Optional[str] = None
    if (market_unusable and model_unusable) or (
        model_score < config.minimum_source_reliability
        and market_score < config.minimum_source_reliability
    ):
        forced_winner = "ABSTAIN"
        codes.append("BOTH_SOURCES_UNRELIABLE")
    elif market.valid_book_count < config.minimum_books or market.stale:
        forced_winner = "MODEL"
    elif prediction.out_of_distribution:
        forced_winner = "MARKET"
    elif (
        abs(market.movement) >= config.coherent_movement_threshold
        and market.movement_coherence >= config.coherent_book_fraction
    ):
        forced_winner = "MARKET"
        codes.append("COHERENT_MARKET_MOVEMENT")

    return SourceAssessment(
        rank_delta=rank_delta,
        material=material,
        model_score=model_score,
        market_score=market_score,
        forced_winner=forced_winner,
        reason_codes=codes,
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
    assessment = _assess_sources(
        prediction,
        market,
        as_of,
        model_rank,
        market_rank,
        config,
    )
    model_weight = assessment.model_score
    market_weight = assessment.market_score
    codes = list(assessment.reason_codes)

    if assessment.forced_winner == "ABSTAIN":
        return Decision(
            event_id=prediction.event_id,
            selection=prediction.selection,
            model_probability=prediction.model_probability,
            market_probability=market.probability,
            reconciled_probability=None,
            model_reliability=assessment.model_score,
            market_reliability=assessment.market_score,
            model_weight=0.0,
            market_weight=0.0,
            winner="ABSTAIN",
            decision_confidence=0.0,
            material_conflict=assessment.material,
            model_rank=model_rank,
            market_rank=market_rank,
            rank_delta=assessment.rank_delta,
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
    if assessment.forced_winner == "MODEL":
        model_weight *= config.forced_winner_multiplier
        market_weight *= config.forced_loser_multiplier
    elif assessment.forced_winner == "MARKET":
        market_weight *= config.forced_winner_multiplier
        model_weight *= config.forced_loser_multiplier

    winner = "MODEL" if model_weight >= market_weight else "MARKET"
    if assessment.forced_winner is None:
        codes.append("MODEL_HIGHER_RELIABILITY" if winner == "MODEL" else "MARKET_HIGHER_RELIABILITY")
    if not assessment.material:
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
        model_reliability=assessment.model_score,
        market_reliability=assessment.market_score,
        model_weight=model_weight,
        market_weight=market_weight,
        winner=winner,
        decision_confidence=decision_confidence,
        material_conflict=assessment.material,
        model_rank=model_rank,
        market_rank=market_rank,
        rank_delta=assessment.rank_delta,
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
