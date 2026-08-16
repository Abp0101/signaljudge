import unittest

from signaljudge.models import Prediction, ValidationError


VALID = {
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


class PredictionValidationTests(unittest.TestCase):
    def test_valid_prediction(self):
        prediction = Prediction.from_dict(VALID)
        self.assertEqual(prediction.selection, "Home")
        self.assertAlmostEqual(prediction.model_probability, 0.65)

    def test_rejects_probability_outside_range(self):
        data = dict(VALID, model_probability=1.2)
        with self.assertRaises(ValidationError):
            Prediction.from_dict(data)

    def test_rejects_selection_not_in_event(self):
        data = dict(VALID, selection="Someone else")
        with self.assertRaises(ValidationError):
            Prediction.from_dict(data)

    def test_rejects_unsafe_identifier(self):
        data = dict(VALID, event_id="../../secrets")
        with self.assertRaises(ValidationError):
            Prediction.from_dict(data)


if __name__ == "__main__":
    unittest.main()

