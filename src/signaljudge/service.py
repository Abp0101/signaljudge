"""Stateful orchestration for fetch/validate/compare/decide/persist/rank/report."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from signaljudge.decision import DecisionConfig, reconcile, unresolved_decision
from signaljudge.models import Decision, Prediction, RunResult, isoformat
from signaljudge.odds import (
    MarketQualityConfig,
    evaluated_at,
    fetched_at,
    normalize_all_detailed,
)
from signaljudge.state import StateStore


def _ranks(values: Mapping[str, float]) -> Dict[str, int]:
    ordered = sorted(values, key=lambda key: (-values[key], key))
    return {event_id: index + 1 for index, event_id in enumerate(ordered)}


def _context_key(predictions: List[Prediction], snapshot: Mapping[str, Any]) -> str:
    sports = ",".join(sorted({item.sport_key for item in predictions}))
    models = ",".join(sorted({item.model_version for item in predictions}))
    region = str(snapshot.get("region", "unspecified"))
    return f"sports={sports}|models={models}|market=h2h|region={region}"


def _prediction_payload(item: Prediction) -> Dict[str, Any]:
    return {
        "event_id": item.event_id,
        "sport_key": item.sport_key,
        "commence_time": isoformat(item.commence_time),
        "home_team": item.home_team,
        "away_team": item.away_team,
        "selection": item.selection,
        "model_probability": item.model_probability,
        "historical_accuracy": item.historical_accuracy,
        "historical_sample_size": item.historical_sample_size,
        "calibration_error": item.calibration_error,
        "generated_at": isoformat(item.generated_at),
        "model_version": item.model_version,
        "out_of_distribution": item.out_of_distribution,
        "source_data_at": isoformat(item.source_data_at) if item.source_data_at else None,
    }


class ReconciliationService:
    def __init__(
        self,
        store: StateStore,
        config: DecisionConfig = DecisionConfig(),
        market_config: MarketQualityConfig = MarketQualityConfig(),
    ):
        self.store = store
        self.config = config
        self.market_config = market_config

    def run(
        self,
        predictions: List[Prediction],
        snapshot: Mapping[str, Any],
        mode: str,
        previous_snapshot: Optional[Mapping[str, Any]] = None,
    ) -> RunResult:
        context_key = _context_key(predictions, snapshot)
        if previous_snapshot is None:
            candidate = self.store.latest_snapshot(context_key)
            if candidate is not None and evaluated_at(candidate) < evaluated_at(snapshot):
                previous_snapshot = candidate
        canonical_input = json.dumps(
            {
                "predictions": [_prediction_payload(item) for item in predictions],
                "snapshot": snapshot,
                "previous_snapshot": previous_snapshot,
                "decision_config": asdict(self.config),
                "market_config": asdict(self.market_config),
                "context_key": context_key,
                "mode": mode,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        content_hash = hashlib.sha256(canonical_input.encode("utf-8")).hexdigest()
        existing = self.store.find_run(content_hash)
        if existing:
            decisions = self.store.load_run(existing)
            warnings = self.store.load_run_warnings(existing)
            return self._result(existing, mode, snapshot, decisions, warnings, reused=True)

        markets, warnings, errors = normalize_all_detailed(
            snapshot,
            predictions,
            previous_snapshot,
            self.market_config,
        )
        all_model_ranks = _ranks({p.event_id: p.model_probability for p in predictions})
        model_ranks = _ranks(
            {p.event_id: p.model_probability for p in predictions if p.event_id in markets}
        )
        market_ranks = _ranks({event_id: market.probability for event_id, market in markets.items()})
        previous_decisions = self.store.latest_decisions(context_key)
        timestamp = evaluated_at(snapshot)
        decisions: List[Decision] = []
        for prediction in predictions:
            market = markets.get(prediction.event_id)
            if market is None:
                decisions.append(
                    unresolved_decision(
                        prediction,
                        timestamp,
                        all_model_ranks[prediction.event_id],
                        errors.get(prediction.event_id, "market normalization failed"),
                        previous_decisions.get(prediction.event_id),
                        self.config,
                    )
                )
                continue
            decisions.append(
                reconcile(
                    prediction=prediction,
                    market=market,
                    as_of=timestamp,
                    model_rank=model_ranks[prediction.event_id],
                    market_rank=market_ranks[prediction.event_id],
                    previous=previous_decisions.get(prediction.event_id),
                    config=self.config,
                )
            )
        resolved = [item for item in decisions if item.reconciled_probability is not None]
        unresolved = [item for item in decisions if item.reconciled_probability is None]
        resolved.sort(key=lambda item: (-float(item.reconciled_probability), item.event_id))
        unresolved.sort(key=lambda item: item.event_id)
        decisions = resolved + unresolved
        for index, decision in enumerate(resolved, 1):
            decision.final_rank = index

        run_id = f"run_{content_hash[:16]}"
        self.store.persist_run(
            run_id=run_id,
            created_at=isoformat(datetime.now(timezone.utc)),
            mode=mode,
            context_key=context_key,
            odds_fetched_at=isoformat(fetched_at(snapshot)),
            content_hash=content_hash,
            snapshot=snapshot,
            decisions=decisions,
            warnings=warnings,
        )
        return self._result(run_id, mode, snapshot, decisions, warnings)

    @staticmethod
    def _result(
        run_id: str,
        mode: str,
        snapshot: Mapping[str, Any],
        decisions: List[Decision],
        warnings: List[str],
        reused: bool = False,
    ) -> RunResult:
        counts = {"MODEL": 0, "MARKET": 0, "ABSTAIN": 0}
        for decision in decisions:
            counts[decision.winner] += 1
        return RunResult(
            run_id=run_id,
            mode=mode,
            odds_fetched_at=str(snapshot.get("fetched_at", "unknown")),
            decisions=decisions,
            material_conflicts=sum(1 for item in decisions if item.material_conflict),
            source_counts=counts,
            reused=reused,
            warnings=warnings,
        )
