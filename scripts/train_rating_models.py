#!/usr/bin/env python3
"""Train the reproducible EPL rating artifact from result-only CSV fields."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple


SOURCES = (
    "https://www.football-data.co.uk/mmz4281/2425/E0.csv",
    "https://www.football-data.co.uk/mmz4281/2425/E1.csv",
    "https://www.football-data.co.uk/mmz4281/2526/E0.csv",
    "https://www.football-data.co.uk/mmz4281/2526/E1.csv",
)
FIELDS_USED = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"]
ALIASES = {
    "Brighton and Hove Albion": "Brighton",
    "Coventry City": "Coventry",
    "Hull City": "Hull",
    "Ipswich Town": "Ipswich",
    "Leeds United": "Leeds",
    "Manchester City": "Man City",
    "Manchester United": "Man United",
    "Newcastle United": "Newcastle",
    "Nottingham Forest": "Nott'm Forest",
    "Sheffield United": "Sheffield United",
    "Tottenham Hotspur": "Tottenham",
    "West Ham United": "West Ham",
    "Wolverhampton Wanderers": "Wolves",
}


@dataclass(frozen=True)
class Match:
    date: datetime
    home: str
    away: str
    home_goals: int
    away_goals: int
    result: str


@dataclass(frozen=True)
class Parameters:
    k_factor: float
    home_advantage: float
    draw_base: float
    draw_scale: float
    minimum_draw: float = 0.14
    maximum_draw: float = 0.32


def fetch_source(url: str) -> Tuple[bytes, str]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "text/csv", "User-Agent": "SignalJudgeModelTrainer/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read(5 * 1024 * 1024 + 1)
    if len(body) > 5 * 1024 * 1024:
        raise RuntimeError(f"source exceeded 5 MiB: {url}")
    return body, hashlib.sha256(body).hexdigest()


def parse_matches(body: bytes) -> List[Match]:
    text = body.decode("utf-8-sig", errors="strict")
    rows = csv.DictReader(io.StringIO(text))
    matches: List[Match] = []
    for row in rows:
        if not all(row.get(field) not in {None, ""} for field in FIELDS_USED):
            continue
        try:
            date = datetime.strptime(str(row["Date"]), "%d/%m/%Y").replace(
                tzinfo=timezone.utc
            )
            home_goals = int(row["FTHG"])
            away_goals = int(row["FTAG"])
        except (TypeError, ValueError):
            continue
        result = str(row["FTR"])
        if result not in {"H", "D", "A"}:
            continue
        matches.append(
            Match(
                date=date,
                home=str(row["HomeTeam"]).strip(),
                away=str(row["AwayTeam"]).strip(),
                home_goals=home_goals,
                away_goals=away_goals,
                result=result,
            )
        )
    return matches


def probabilities(home_rating: float, away_rating: float, params: Parameters) -> Dict[str, float]:
    gap = home_rating + params.home_advantage - away_rating
    home_without_draw = 1.0 / (1.0 + 10.0 ** (-gap / 400.0))
    draw = max(
        params.minimum_draw,
        min(params.maximum_draw, params.draw_base - abs(gap) / params.draw_scale),
    )
    return {
        "H": (1.0 - draw) * home_without_draw,
        "D": draw,
        "A": (1.0 - draw) * (1.0 - home_without_draw),
    }


def update_ratings(
    ratings: Dict[str, float], appearances: Dict[str, int], match: Match, params: Parameters
) -> None:
    home_rating = ratings.setdefault(match.home, 1500.0)
    away_rating = ratings.setdefault(match.away, 1500.0)
    expected = 1.0 / (
        1.0 + 10.0 ** (-(home_rating + params.home_advantage - away_rating) / 400.0)
    )
    actual = 1.0 if match.result == "H" else 0.5 if match.result == "D" else 0.0
    goal_multiplier = 1.0 + 0.15 * max(0, abs(match.home_goals - match.away_goals) - 1)
    change = params.k_factor * goal_multiplier * (actual - expected)
    ratings[match.home] = home_rating + change
    ratings[match.away] = away_rating - change
    appearances[match.home] = appearances.get(match.home, 0) + 1
    appearances[match.away] = appearances.get(match.away, 0) + 1


def train(matches: Sequence[Match], params: Parameters) -> Tuple[Dict[str, float], Dict[str, int]]:
    ratings: Dict[str, float] = {}
    appearances: Dict[str, int] = {}
    for match in matches:
        update_ratings(ratings, appearances, match, params)
    return ratings, appearances


def evaluate(
    history: Sequence[Match], evaluation: Sequence[Match], params: Parameters
) -> Mapping[str, float]:
    ratings, appearances = train(history, params)
    records: List[Tuple[Dict[str, float], str, str, float]] = []
    for match in evaluation:
        prediction = probabilities(
            ratings.get(match.home, 1500.0), ratings.get(match.away, 1500.0), params
        )
        selected, selected_probability = max(
            prediction.items(), key=lambda item: (item[1], item[0])
        )
        records.append((prediction, match.result, selected, selected_probability))
        update_ratings(ratings, appearances, match, params)
    if not records:
        raise RuntimeError("evaluation split is empty")
    accuracy = sum(selected == actual for _, actual, selected, _ in records) / len(records)
    brier = sum(
        sum((forecast[key] - (1.0 if actual == key else 0.0)) ** 2 for key in ("H", "D", "A"))
        / 3.0
        for forecast, actual, _, _ in records
    ) / len(records)
    log_loss = -sum(
        math.log(max(0.001, forecast[actual])) for forecast, actual, _, _ in records
    ) / len(records)
    calibration_error = 0.0
    for lower in [index / 10.0 for index in range(10)]:
        bucket = [
            (selected_probability, selected == actual)
            for _, actual, selected, selected_probability in records
            if lower <= selected_probability < lower + 0.1
        ]
        if bucket:
            confidence = sum(item[0] for item in bucket) / len(bucket)
            observed = sum(item[1] for item in bucket) / len(bucket)
            calibration_error += len(bucket) / len(records) * abs(confidence - observed)
    return {
        "accuracy": accuracy,
        "brier": brier,
        "log_loss": log_loss,
        "calibration_error": min(0.5, calibration_error),
        "sample_size": len(records),
    }


def candidates() -> Iterable[Parameters]:
    for k_factor in (16.0, 24.0, 32.0):
        for home_advantage in (45.0, 70.0, 95.0):
            for draw_base in (0.25, 0.28, 0.31):
                for draw_scale in (1400.0, 2000.0, 2600.0):
                    yield Parameters(k_factor, home_advantage, draw_base, draw_scale)


def train_artifact(output: Path) -> Mapping[str, object]:
    matches: List[Match] = []
    provenance: List[Dict[str, object]] = []
    for url in SOURCES:
        body, digest = fetch_source(url)
        source_matches = parse_matches(body)
        if not source_matches:
            raise RuntimeError(f"no valid result rows in {url}")
        matches.extend(source_matches)
        provenance.append(
            {
                "url": url,
                "sha256": digest,
                "rows": len(source_matches),
                "fields_used": FIELDS_USED,
            }
        )
    matches.sort(key=lambda item: (item.date, item.home, item.away))
    training_end = int(len(matches) * 0.70)
    tuning_end = int(len(matches) * 0.85)
    training = matches[:training_end]
    tuning = matches[training_end:tuning_end]
    test = matches[tuning_end:]
    best = min(
        candidates(),
        key=lambda params: float(evaluate(training, tuning, params)["log_loss"]),
    )
    validation = evaluate(matches[:tuning_end], test, best)
    ratings, appearances = train(matches, best)
    trained_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    artifact: Mapping[str, object] = {
        "schema_version": 1,
        "model_type": "elo_rating",
        "sport_key": "soccer_epl",
        "model_version": "elo-soccer-v1-2026-08",
        "trained_at": trained_at,
        "training_cutoff": matches[-1].date.date().isoformat(),
        "outcome_mode": "three_way",
        "parameters": {
            "k_factor": best.k_factor,
            "home_advantage": best.home_advantage,
            "draw_base": best.draw_base,
            "draw_scale": best.draw_scale,
            "minimum_draw": best.minimum_draw,
            "maximum_draw": best.maximum_draw,
            "minimum_team_games": 10,
        },
        "validation": validation,
        "ratings": {name: round(value, 6) for name, value in sorted(ratings.items())},
        "appearances": dict(sorted(appearances.items())),
        "aliases": ALIASES,
        "sources": provenance,
        "training_protocol": {
            "split": "chronological 70% train / 15% tune / 15% untouched test",
            "selection_metric": "multiclass log loss",
            "odds_columns_used": False,
            "notes": "Only the six allowlisted result fields are parsed from each source.",
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="models/soccer_epl.model.json")
    args = parser.parse_args()
    artifact = train_artifact(Path(args.output).resolve())
    validation = artifact["validation"]
    print(f"wrote {args.output}")
    print(
        "test metrics: "
        f"accuracy={validation['accuracy']:.3f} "
        f"brier={validation['brier']:.3f} "
        f"log_loss={validation['log_loss']:.3f} "
        f"ece={validation['calibration_error']:.3f} "
        f"n={validation['sample_size']}"
    )


if __name__ == "__main__":
    main()
