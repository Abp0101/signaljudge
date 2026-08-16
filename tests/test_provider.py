import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from signaljudge.models import ValidationError
from signaljudge.provider import API_BASE_URL, LiveOddsProvider


class FakeResponse:
    status = 200
    headers = {
        "x-requests-remaining": "499",
        "x-requests-used": "1",
        "x-requests-last": "1",
    }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, _limit):
        return json.dumps(
            [
                {
                    "id": "event-123",
                    "sport_key": "baseball_mlb",
                    "sport_title": "MLB",
                    "commence_time": "2026-08-17T18:00:00Z",
                    "home_team": "Home",
                    "away_team": "Away",
                    "bookmakers": [
                        {
                            "key": "draftkings",
                            "last_update": "2026-08-17T17:00:00Z",
                            "markets": [
                                {
                                    "key": "h2h",
                                    "last_update": "2026-08-17T17:01:00Z",
                                    "outcomes": [
                                        {"name": "Home", "price": 1.8},
                                        {"name": "Away", "price": 2.1},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        ).encode("utf-8")


class ProviderSecurityTests(unittest.TestCase):
    @patch("urllib.request.urlopen", return_value=FakeResponse())
    def test_uses_v4_contract_and_normalizes_nested_markets(self, mocked_open):
        with tempfile.TemporaryDirectory() as directory:
            provider = LiveOddsProvider(Path(directory))
            payload = provider.fetch("baseball_mlb", api_key="a" * 32)
        request = mocked_open.call_args.args[0]
        self.assertTrue(request.full_url.startswith(f"{API_BASE_URL}/sports/baseball_mlb/odds/"))
        self.assertIn("apiKey=" + "a" * 32, request.full_url)
        self.assertEqual(payload["data"][0]["event_id"], "event-123")
        self.assertEqual(payload["data"][0]["books"][0]["market"], "h2h")
        self.assertEqual(payload["quota"]["remaining"], "499")

    @patch("urllib.request.urlopen")
    def test_http_errors_do_not_chain_secret_bearing_url(self, mocked_open):
        secret = "b" * 32
        mocked_open.side_effect = HTTPError(
            f"{API_BASE_URL}/sports/baseball_mlb/odds/?apiKey={secret}",
            401,
            "Unauthorized",
            {},
            None,
        )
        with tempfile.TemporaryDirectory() as directory:
            provider = LiveOddsProvider(Path(directory), max_attempts=1)
            with self.assertRaises(ValidationError) as raised:
                provider.fetch("baseball_mlb", api_key=secret)
        self.assertNotIn(secret, str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    def test_rejects_non_allowlisted_sport(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = LiveOddsProvider(Path(directory))
            with self.assertRaises(ValidationError):
                provider.fetch("https://attacker.invalid", api_key="not-a-real-key")

    def test_missing_secret_fails_before_network(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            provider = LiveOddsProvider(Path(directory))
            with self.assertRaises(ValidationError):
                provider.fetch("baseball_mlb")


if __name__ == "__main__":
    unittest.main()
