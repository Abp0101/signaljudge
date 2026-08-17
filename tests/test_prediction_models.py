import copy
import unittest
from datetime import datetime, timezone
from pathlib import Path

from signaljudge.io import load_json
from signaljudge.models import ValidationError
from signaljudge.prediction_models import Fixture, RatingModel
from signaljudge.rating import elo_outcome_probabilities
from scripts.train_rating_models import Parameters, probabilities


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "models" / "soccer_epl.model.json"


class RatingModelTests(unittest.TestCase):
    def test_training_and_inference_share_the_same_elo_forecast(self):
        parameters = Parameters(24.0, 70.0, 0.28, 2000.0)
        training = probabilities(1610.0, 1490.0, parameters)
        serving = elo_outcome_probabilities(
            1610.0,
            1490.0,
            parameters.home_advantage,
            parameters.draw_base,
            parameters.draw_scale,
            parameters.minimum_draw,
            parameters.maximum_draw,
        )
        self.assertEqual(training, serving)

    def setUp(self):
        self.model = RatingModel.load(ARTIFACT)
        self.generated_at = datetime(2026, 8, 16, 15, 0, tzinfo=timezone.utc)

    @staticmethod
    def event(books=None):
        return {
            "event_id": "live-epl-event",
            "sport_key": "soccer_epl",
            "home_team": "Arsenal",
            "away_team": "Coventry City",
            "start_time": "2026-08-21T19:00:00Z",
            "books": books or [],
        }

    def test_model_artifact_has_measured_holdout_metrics_and_provenance(self):
        self.assertEqual(self.model.sport_key, "soccer_epl")
        self.assertEqual(self.model.metrics.sample_size, 280)
        self.assertGreater(self.model.metrics.accuracy, 1 / 3)
        self.assertLess(self.model.metrics.calibration_error, 0.10)
        self.assertEqual(len(self.model.sources), 4)

    def test_prediction_is_independent_of_bookmaker_prices(self):
        short_price = self.event(
            [{"outcomes": [{"name": "Arsenal", "price": 1.2}]}]
        )
        long_price = self.event(
            [{"outcomes": [{"name": "Arsenal", "price": 9.0}]}]
        )
        first = self.model.predict([Fixture.from_event(short_price)], self.generated_at)[0]
        second = self.model.predict([Fixture.from_event(long_price)], self.generated_at)[0]
        self.assertEqual(first.selection, second.selection)
        self.assertEqual(first.model_probability, second.model_probability)
        self.assertEqual(first.model_version, "elo-soccer-v1-2026-08")
        self.assertEqual(first.source_data_at.date().isoformat(), "2026-05-24")

    def test_known_aliases_are_in_distribution_and_unknown_team_is_flagged(self):
        known = self.model.predict(
            [Fixture.from_event(self.event())], self.generated_at
        )[0]
        unknown_event = self.event()
        unknown_event["away_team"] = "Unknown Athletic"
        unknown = self.model.predict(
            [Fixture.from_event(unknown_event)], self.generated_at
        )[0]
        self.assertFalse(known.out_of_distribution)
        self.assertTrue(unknown.out_of_distribution)

    def test_started_fixture_is_not_backfilled_with_a_pregame_prediction(self):
        fixture = Fixture.from_event(self.event())
        after_start = datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc)
        self.assertEqual(self.model.predict([fixture], after_start), [])

    def test_invalid_artifact_fails_closed(self):
        payload = {
            "schema_version": 1,
            "model_type": "elo_rating",
            "sport_key": "soccer_epl",
        }
        with self.assertRaises(ValidationError):
            RatingModel.from_dict(copy.deepcopy(payload))

    def test_malformed_source_provenance_fails_closed(self):
        payload = load_json(ARTIFACT)
        payload["sources"][0]["rows"] = -1
        with self.assertRaises(ValidationError):
            RatingModel.from_dict(copy.deepcopy(payload))
        payload = load_json(ARTIFACT)
        payload["sources"][0]["fields_used"] = "Date"
        with self.assertRaises(ValidationError):
            RatingModel.from_dict(copy.deepcopy(payload))


if __name__ == "__main__":
    unittest.main()
