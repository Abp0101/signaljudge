"""Shared, deterministic Elo mathematics for model training and inference."""

from __future__ import annotations

from typing import Dict


DEFAULT_RATING = 1500.0
ELO_SCALE = 400.0


def expected_home_score(
    home_rating: float,
    away_rating: float,
    home_advantage: float,
) -> float:
    rating_gap = home_rating + home_advantage - away_rating
    return 1.0 / (1.0 + 10.0 ** (-rating_gap / ELO_SCALE))


def elo_outcome_probabilities(
    home_rating: float,
    away_rating: float,
    home_advantage: float,
    draw_base: float,
    draw_scale: float,
    minimum_draw: float,
    maximum_draw: float,
    three_way: bool = True,
) -> Dict[str, float]:
    rating_gap = home_rating + home_advantage - away_rating
    home_without_draw = expected_home_score(home_rating, away_rating, home_advantage)
    if not three_way:
        return {"H": home_without_draw, "A": 1.0 - home_without_draw}
    draw = max(
        minimum_draw,
        min(maximum_draw, draw_base - abs(rating_gap) / draw_scale),
    )
    return {
        "H": (1.0 - draw) * home_without_draw,
        "D": draw,
        "A": (1.0 - draw) * (1.0 - home_without_draw),
    }
