"""Objective replay evaluation against both blind source baselines."""

from __future__ import annotations

import math
from typing import Dict, List, Mapping, Tuple

from signaljudge.models import Decision


def _score(probabilities: List[float], outcomes: List[int]) -> Dict[str, float]:
    if not probabilities:
        raise ValueError("at least one settled event is required")
    if len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must have equal length")
    clipped = [max(1e-6, min(1 - 1e-6, value)) for value in probabilities]
    count = len(clipped)
    brier = sum((p - y) ** 2 for p, y in zip(clipped, outcomes)) / count
    log_loss = -sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in zip(clipped, outcomes)) / count
    accuracy = sum((p >= 0.5) == bool(y) for p, y in zip(clipped, outcomes)) / count
    return {"brier": brier, "log_loss": log_loss, "accuracy": accuracy, "sample_size": float(count)}


def evaluate(
    decisions: List[Decision], results: Mapping[str, str]
) -> Tuple[Dict[str, Dict[str, float]], List[Dict[str, object]]]:
    probabilities = {"MODEL": [], "MARKET": [], "AGENT": []}
    outcomes: List[int] = []
    cases: List[Dict[str, object]] = []
    for decision in decisions:
        if (
            decision.event_id not in results
            or decision.status != "RECONCILED"
            or decision.market_probability is None
            or decision.reconciled_probability is None
        ):
            continue
        outcome = 1 if results[decision.event_id] == decision.selection else 0
        outcomes.append(outcome)
        probabilities["MODEL"].append(decision.model_probability)
        probabilities["MARKET"].append(decision.market_probability)
        probabilities["AGENT"].append(decision.reconciled_probability)
        model_correct = (decision.model_probability >= 0.5) == bool(outcome)
        market_correct = (decision.market_probability >= 0.5) == bool(outcome)
        agent_correct = (decision.reconciled_probability >= 0.5) == bool(outcome)
        cases.append(
            {
                "event_id": decision.event_id,
                "selection": decision.selection,
                "outcome": outcome,
                "winner": decision.winner,
                "model_correct": model_correct,
                "market_correct": market_correct,
                "agent_correct": agent_correct,
                "corrected_model_only": (not model_correct) and agent_correct,
                "corrected_market_only": (not market_correct) and agent_correct,
            }
        )
    metrics = {source: _score(values, outcomes) for source, values in probabilities.items()}
    return metrics, cases
