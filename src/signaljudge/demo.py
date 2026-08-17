"""Shared reproducible replay use case for the CLI and local application."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping

from signaljudge.io import load_json, load_predictions, load_results
from signaljudge.models import Prediction, RunResult
from signaljudge.service import ReconciliationService
from signaljudge.state import StateStore


@dataclass(frozen=True)
class DemoReplay:
    predictions: List[Prediction]
    opening_snapshot: Mapping[str, Any]
    latest_snapshot: Mapping[str, Any]
    results: Dict[str, str]
    opening: RunResult
    latest: RunResult


def run_demo_replay(
    store: StateStore,
    demo_dir: Path,
    mode_prefix: str,
) -> DemoReplay:
    predictions = load_predictions(demo_dir / "model_predictions.json")
    opening_snapshot = load_json(demo_dir / "odds_opening.json")
    latest_snapshot = load_json(demo_dir / "odds_latest.json")
    results = load_results(demo_dir / "results.json")
    service = ReconciliationService(store)
    opening = service.run(
        predictions,
        opening_snapshot,
        mode=f"{mode_prefix}-opening",
    )
    latest = service.run(
        predictions,
        latest_snapshot,
        mode=f"{mode_prefix}-latest",
        previous_snapshot=opening_snapshot,
    )
    return DemoReplay(
        predictions=predictions,
        opening_snapshot=opening_snapshot,
        latest_snapshot=latest_snapshot,
        results=results,
        opening=opening,
        latest=latest,
    )
