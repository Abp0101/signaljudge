import unittest
from datetime import datetime, timezone

from signaljudge.models import Prediction
from signaljudge.odds import implied_probability, normalize_market


def prediction():
    return Prediction.from_dict(
        {
            "event_id": "event-1",
            "sport_key": "baseball_mlb",
            "commence_time": "2026-08-16T20:00:00Z",
            "home_team": "Home",
            "away_team": "Away",
            "selection": "Home",
            "model_probability": 0.65,
            "historical_accuracy": 0.70,
            "historical_sample_size": 200,
            "calibration_error": 0.04,
            "generated_at": "2026-08-16T10:00:00Z",
            "model_version": "v1",
        }
    )


def snapshot(probs, updated_at="2026-08-16T17:58:00Z"):
    books = []
    for index, value in enumerate(probs):
        books.append(
            {
                "book": f"book{index}",
                "market": "h2h",
                "updated_at": updated_at,
                "outcomes": [
                    {"name": "Home", "price": 1 / (value * 1.04)},
                    {"name": "Away", "price": 1 / ((1 - value) * 1.04)},
                ],
            }
        )
    return {
        "success": True,
        "fetched_at": "2026-08-16T18:00:00Z",
        "odds_format": "decimal",
        "data": [
            {
                "event_id": "event-1",
                "home_team": "Home",
                "away_team": "Away",
                "start_time": "2026-08-16T20:00:00Z",
                "books": books,
            }
        ],
    }


class OddsTests(unittest.TestCase):
    def test_decimal_and_american_conversion(self):
        self.assertAlmostEqual(implied_probability(2.0, "decimal"), 0.5)
        self.assertAlmostEqual(implied_probability(-150, "american"), 0.6)
        self.assertAlmostEqual(implied_probability(150, "american"), 0.4)

    def test_removes_vig_and_uses_consensus(self):
        market = normalize_market(snapshot([0.59, 0.60, 0.61, 0.60, 0.60]), prediction())
        self.assertAlmostEqual(market.probability, 0.60, places=5)
        self.assertGreater(market.quality, 0.8)

    def test_rejects_isolated_outlier(self):
        market = normalize_market(snapshot([0.43, 0.43, 0.43, 0.43, 0.78]), prediction())
        self.assertAlmostEqual(market.probability, 0.43, places=5)
        self.assertEqual(market.rejected_books, ["book4"])

    def test_detects_stale_market(self):
        market = normalize_market(snapshot([0.55] * 5, "2026-08-16T14:00:00Z"), prediction())
        self.assertTrue(market.stale)
        self.assertLess(market.quality, 0.71)

    def test_detects_coherent_movement(self):
        opening = normalize_market(snapshot([0.62] * 5), prediction())
        latest = normalize_market(snapshot([0.42] * 5), prediction(), opening)
        self.assertAlmostEqual(latest.movement, -0.20, places=4)
        self.assertEqual(latest.movement_coherence, 1.0)


if __name__ == "__main__":
    unittest.main()
