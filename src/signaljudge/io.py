"""Bounded JSON input/output helpers."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from signaljudge.models import Prediction, ValidationError


MAX_INPUT_BYTES = 5 * 1024 * 1024


def load_json(path: Path) -> Any:
    path = path.resolve()
    if not path.is_file():
        raise ValidationError(f"input file does not exist: {path}")
    size = path.stat().st_size
    if size > MAX_INPUT_BYTES:
        raise ValidationError(f"input exceeds {MAX_INPUT_BYTES} bytes")
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"could not read valid JSON from {path.name}") from exc


def load_predictions(path: Path) -> List[Prediction]:
    payload = load_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValidationError("prediction file must use schema_version 1")
    rows = payload.get("predictions")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise ValidationError("predictions must contain between 1 and 1000 records")
    predictions = [Prediction.from_dict(row) for row in rows if isinstance(row, dict)]
    if len(predictions) != len(rows):
        raise ValidationError("every prediction must be a JSON object")
    ids = [prediction.event_id for prediction in predictions]
    if len(ids) != len(set(ids)):
        raise ValidationError("prediction event_id values must be unique")
    return predictions


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_results(path: Path) -> Dict[str, str]:
    payload = load_json(path)
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValidationError("results must be a list")
    results: Dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("event_id"), str):
            raise ValidationError("result records must include event_id")
        winner = row.get("winner")
        if not isinstance(winner, str) or not winner.strip():
            raise ValidationError("result records must include winner")
        results[row["event_id"]] = winner.strip()
    return results
