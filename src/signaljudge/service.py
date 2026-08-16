"""Stateful orchestration for fetch/validate/compare/decide/persist/rank/report."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from signaljudge.decision import DecisionConfig, reconcile
from signaljudge.models import Decision, Prediction, RunResult, isoformat
from signaljudge.odds import fetched_at, normalize_all
from signaljudge.state import StateStore


def _ranks(values: Mapping[str, float]) -> Dict[str, int]:
    ordered = sorted(values, key=lambda key: (-values[key], key))
    return {event_id: index + 1 for index, event_id in enumerate(ordered)}


class ReconciliationService:
    def __init__(self, store: StateStore, config: DecisionConfig = DecisionConfig()):
        self.store = store
        self.config = config

    def run(
        self,
        predictions: List[Prediction],
        snapshot: Mapping[str, Any],
        mode: str,
        previous_snapshot: Optional[Mapping[str, Any]] = None,
    ) -> RunResult:
        if previous_snapshot is None:
            previous_snapshot = self.store.latest_snapshot()
        canonical_input = json.dumps(
            {
                "predictions": [
                    {
                        "event_id": item.event_id,
                        "model_probability": item.model_probability,
                        "generated_at": isoformat(item.generated_at),
                        "model_version": item.model_version,
                    }
                    for item in predictions
                ],
                "snapshot": snapshot,
                "config": self.config.__dict__,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        content_hash = hashlib.sha256(canonical_input.encode("utf-8")).hexdigest()
        existing = self.store.find_run(content_hash)
        if existing:
            decisions = self.store.load_run(existing)
            return self._result(existing, mode, snapshot, decisions, [], reused=True)

        markets, warnings = normalize_all(snapshot, predictions, previous_snapshot)
        model_ranks = _ranks({p.event_id: p.model_probability for p in predictions})
        market_ranks = _ranks({event_id: market.probability for event_id, market in markets.items()})
        previous_decisions = self.store.latest_decisions()
        timestamp = fetched_at(snapshot)
        decisions: List[Decision] = []
        for prediction in predictions:
            market = markets.get(prediction.event_id)
            if market is None:
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
        decisions.sort(key=lambda item: (-item.reconciled_probability, item.event_id))
        for index, decision in enumerate(decisions, 1):
            decision.final_rank = index

        run_id = f"run_{content_hash[:16]}"
        self.store.persist_run(
            run_id=run_id,
            created_at=isoformat(datetime.now(timezone.utc)),
            mode=mode,
            odds_fetched_at=isoformat(timestamp),
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
        counts = {"MODEL": 0, "MARKET": 0}
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

