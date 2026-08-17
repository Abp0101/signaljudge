import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from signaljudge.application import (
    ApplicationConfig,
    ApplicationHTTPServer,
    ApplicationService,
    RefreshRateLimitError,
)
from signaljudge.cli import build_parser, load_api_key_env_file
from signaljudge.io import load_json


ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "demo"
MODELS = ROOT / "models"


class FakeProvider:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = []

    def fetch(self, sport_key, api_key=None, region=None):
        self.calls.append((sport_key, region))
        return self.snapshot


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class ApplicationTests(unittest.TestCase):
    def _service(self, root, with_predictions=True, clock=None, model_dir=None):
        prediction_dir = root / "predictions"
        prediction_dir.mkdir()
        empty_model_dir = root / "models"
        empty_model_dir.mkdir()
        if with_predictions:
            shutil.copy2(
                DEMO / "model_predictions.json",
                prediction_dir / "baseball_mlb.json",
            )
        snapshot = load_json(DEMO / "odds_latest.json")
        for event in snapshot["data"]:
            event.setdefault("sport_key", "baseball_mlb")
        provider = FakeProvider(snapshot)
        config = ApplicationConfig(
            prediction_dir=prediction_dir,
            model_dir=model_dir or empty_model_dir,
            db_path=root / "application.db",
            cache_dir=root / "cache",
            demo_dir=DEMO,
            response_ttl_seconds=300,
            refresh_cooldown_seconds=30,
        )
        return ApplicationService(config, provider=provider, monotonic=clock or Clock()), provider

    def test_live_application_reuses_core_and_exposes_complete_audit_view(self):
        with tempfile.TemporaryDirectory() as directory:
            service, provider = self._service(Path(directory))
            payload = service.live_rankings("baseball_mlb", "us")
        self.assertEqual(provider.calls, [("baseball_mlb", "us")])
        self.assertEqual(payload["total_events"], 8)
        self.assertEqual(payload["reconciled_events"], 8)
        self.assertGreaterEqual(payload["material_conflicts"], 3)
        self.assertTrue(payload["audit"]["valid"])
        self.assertTrue(all(item["prediction_available"] for item in payload["matches"]))
        first = payload["matches"][0]
        self.assertIn(first["winner"], {"MODEL", "MARKET", "ABSTAIN"})
        self.assertTrue(first["home_team"])
        self.assertTrue(first["away_team"])
        self.assertTrue(first["commence_time"].endswith("Z"))
        self.assertTrue(first["rationale"])
        self.assertGreater(first["valid_book_count"], 0)
        self.assertGreaterEqual(first["total_book_count"], first["valid_book_count"])
        self.assertTrue(first["bookmakers"])
        self.assertIsNotNone(first["market_median_age_seconds"])
        self.assertIn(first["market_data_origin"], {"FIXTURE", "LIVE", "CACHE"})

    def test_missing_model_file_keeps_live_fixtures_visible_without_scores(self):
        with tempfile.TemporaryDirectory() as directory:
            service, _provider = self._service(Path(directory), with_predictions=False)
            payload = service.live_rankings("baseball_mlb", "us")
        self.assertEqual(payload["prediction_source"]["status"], "missing")
        self.assertEqual(payload["reconciled_events"], 0)
        self.assertEqual(payload["total_events"], 8)
        self.assertEqual(payload["fetched_at"], "2026-08-16T18:00:00Z")
        self.assertIsNone(payload["run_id"])
        self.assertTrue(all(not item["prediction_available"] for item in payload["matches"]))
        self.assertTrue(
            all(item["status"] == "NO_PREDICTION" for item in payload["matches"])
        )

    def test_unmatched_input_prediction_is_individually_audited(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, _provider = self._service(root)
            path = service.config.prediction_dir / "baseball_mlb.json"
            prediction_file = load_json(path)
            unmatched = dict(prediction_file["predictions"][0])
            unmatched.update(
                {
                    "event_id": "input-only-event",
                    "home_team": "Input Home",
                    "away_team": "Input Away",
                    "selection": "Input Home",
                    "commence_time": "2026-08-18T20:00:00Z",
                }
            )
            prediction_file["predictions"].append(unmatched)
            path.write_text(json.dumps(prediction_file), encoding="utf-8")
            payload = service.live_rankings("baseball_mlb", "us")

        audited = [
            item for item in payload["matches"] if item["event_id"] == "input-only-event"
        ]
        self.assertEqual(payload["total_events"], 8)
        self.assertEqual(payload["prediction_source"]["unmatched"], 1)
        self.assertEqual(len(audited), 1)
        self.assertEqual(audited[0]["status"], "UNRESOLVED")
        self.assertEqual(audited[0]["winner"], "ABSTAIN")
        self.assertIn("no valid matching market evidence", audited[0]["rationale"])
        self.assertTrue(payload["audit"]["valid"])

    def test_malformed_provider_event_is_omitted_from_unpredicted_view(self):
        with tempfile.TemporaryDirectory() as directory:
            service, provider = self._service(Path(directory), with_predictions=False)
            provider.snapshot["data"].append(
                {
                    "event_id": "unsafe-event",
                    "sport_key": "baseball_mlb",
                    "home_team": "<script>" + "x" * 200,
                    "away_team": "Away",
                    "start_time": "not-a-date",
                    "books": [],
                }
            )
            payload = service.live_rankings("baseball_mlb", "us")
        self.assertEqual(payload["total_events"], 8)
        self.assertTrue(any("malformed provider event" in item for item in payload["warnings"]))

    def test_trained_model_generates_exact_live_fixture_predictions(self):
        snapshot = {
            "success": True,
            "source": "test",
            "odds_format": "decimal",
            "fetched_at": "2026-08-16T15:00:00Z",
            "evaluated_at": "2026-08-16T15:00:00Z",
            "data_origin": "LIVE",
            "region": "uk",
            "quota": {"remaining": "99"},
            "data": [
                {
                    "event_id": "epl-live-1",
                    "sport_key": "soccer_epl",
                    "home_team": "Arsenal",
                    "away_team": "Coventry City",
                    "start_time": "2026-08-21T19:00:00Z",
                    "books": [
                        {
                            "book": "one",
                            "market": "h2h",
                            "updated_at": "2026-08-16T14:59:00Z",
                            "outcomes": [
                                {"name": "Arsenal", "price": 1.60},
                                {"name": "Draw", "price": 4.20},
                                {"name": "Coventry City", "price": 5.80},
                            ],
                        },
                        {
                            "book": "two",
                            "market": "h2h",
                            "updated_at": "2026-08-16T14:58:00Z",
                            "outcomes": [
                                {"name": "Arsenal", "price": 1.64},
                                {"name": "Draw", "price": 4.10},
                                {"name": "Coventry City", "price": 5.60},
                            ],
                        },
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, provider = self._service(
                root, with_predictions=False, model_dir=MODELS
            )
            provider.snapshot = snapshot
            payload = service.live_rankings("soccer_epl", "uk")
        self.assertEqual(payload["prediction_source"]["status"], "trained")
        self.assertEqual(payload["prediction_source"]["type"], "trained_local_model")
        self.assertEqual(payload["prediction_source"]["sample_size"], 280)
        self.assertEqual(payload["reconciled_events"], 1)
        self.assertEqual(payload["matches"][0]["selection"], "Arsenal")
        self.assertIsNotNone(payload["matches"][0]["market_probability"])
        self.assertTrue(payload["audit"]["valid"])

    def test_response_cache_protects_quota_and_manual_refresh_is_rate_limited(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock()
            service, provider = self._service(Path(directory), clock=clock)
            first = service.live_rankings("baseball_mlb", "us")
            clock.value += 5
            second = service.live_rankings("baseball_mlb", "us")
            with self.assertRaises(RefreshRateLimitError):
                service.live_rankings("baseball_mlb", "us", force_refresh=True)
            clock.value += 30
            refreshed = service.live_rankings("baseball_mlb", "us", force_refresh=True)
        self.assertFalse(first["response_cache"])
        self.assertTrue(second["response_cache"])
        self.assertFalse(refreshed["response_cache"])
        self.assertEqual(len(provider.calls), 2)

    def test_demo_endpoint_remains_reproducible_without_live_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            service, provider = self._service(Path(directory), with_predictions=False)
            payload = service.demo_rankings()
        self.assertEqual(provider.calls, [])
        self.assertEqual(payload["market_source"]["origin"], "DEMO")
        self.assertEqual(payload["reconciled_events"], 8)
        self.assertGreaterEqual(payload["material_conflicts"], 3)
        self.assertGreater(payload["source_counts"]["MODEL"], 0)
        self.assertGreater(payload["source_counts"]["MARKET"], 0)

    def test_http_surface_requires_same_origin_header_and_sets_security_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            service, _provider = self._service(Path(directory), with_predictions=False)
            try:
                server = ApplicationHTTPServer(("127.0.0.1", 0), service)
            except PermissionError:
                self.skipTest("the execution sandbox does not permit localhost sockets")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request("GET", "/")
                root = connection.getresponse()
                root_body = root.read().decode("utf-8")
                self.assertEqual(root.status, 200)
                self.assertIn("frame-ancestors 'none'", root.getheader("Content-Security-Policy"))
                self.assertIn("Live fixtures", root_body)

                connection.request("GET", "/assets/app.js")
                asset = connection.getresponse()
                asset.read()
                self.assertEqual(asset.status, 200)
                self.assertEqual(asset.getheader("Cache-Control"), "no-store")

                connection.request("GET", "/api/demo")
                forbidden = connection.getresponse()
                forbidden.read()
                self.assertEqual(forbidden.status, 403)

                connection.request(
                    "GET", "/api/demo", headers={"X-SignalJudge-Request": "1"}
                )
                allowed = connection.getresponse()
                body = json.loads(allowed.read())
                self.assertEqual(allowed.status, 200)
                self.assertEqual(body["market_source"]["origin"], "DEMO")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_cli_exposes_application_command(self):
        args = build_parser().parse_args(["app", "--open", "--port", "9000"])
        self.assertEqual(args.command, "app")
        self.assertTrue(args.open)
        self.assertEqual(args.port, 9000)

    def test_dotenv_loader_reads_only_api_key_without_shell_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "IGNORED=$(touch should-not-run)\nTHE_ODDS_API_KEY='safe-test-key'\n",
                encoding="utf-8",
            )
            previous = os.environ.pop("THE_ODDS_API_KEY", None)
            try:
                loaded = load_api_key_env_file(path)
                self.assertTrue(loaded)
                self.assertEqual(os.environ["THE_ODDS_API_KEY"], "safe-test-key")
                self.assertFalse((Path(directory) / "should-not-run").exists())
            finally:
                os.environ.pop("THE_ODDS_API_KEY", None)
                if previous is not None:
                    os.environ["THE_ODDS_API_KEY"] = previous


if __name__ == "__main__":
    unittest.main()
